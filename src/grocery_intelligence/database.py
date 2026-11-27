import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import (
    JSON,
    Column,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
from sqlalchemy.dialects.sqlite import insert
from sqlalchemy.engine import URL

from grocery_intelligence.models import PriceObservation, now

metadata = MetaData()
chains = Table('chains', metadata, Column('id', String, primary_key=True), Column('name', String))
stores = Table('stores', metadata, Column('id', String, primary_key=True),
               Column('chain_id', ForeignKey('chains.id'), nullable=False), Column('name', String),
               Column('address', String))
products = Table('products', metadata, Column('id', String, primary_key=True), Column('identity', JSON))
aliases = Table('product_aliases', metadata, Column('product_id', ForeignKey('products.id'), primary_key=True),
                Column('store_id', ForeignKey('stores.id'), primary_key=True),
                Column('name', String, primary_key=True))
sources = Table('data_sources', metadata, Column('id', String, primary_key=True),
                Column('store_id', ForeignKey('stores.id')), Column('config', JSON),
                Column('etag', String), Column('modified', String), Column('last_hash', String),
                Column('pause_until', String), Column('last_attempt', String))
artifacts = Table('source_artifacts', metadata, Column('id', String, primary_key=True),
                  Column('source_id', ForeignKey('data_sources.id')), Column('sha256', String),
                  Column('created_at', String))
prices = Table('price_observations', metadata, Column('id', String, primary_key=True),
               Column('store_id', ForeignKey('stores.id')), Column('product_id', ForeignKey('products.id')),
               Column('artifact_id', ForeignKey('source_artifacts.id')), Column('row_number', Integer),
               Column('observed_at', String), Column('record', JSON), UniqueConstraint('artifact_id', 'row_number'))
promotions = Table('promotions', metadata, Column('observation_id', ForeignKey('price_observations.id'), primary_key=True),
                   Column('valid_from', String), Column('valid_until', String))
runs = Table('crawl_runs', metadata, Column('id', Integer, primary_key=True),
             Column('source_id', ForeignKey('data_sources.id')), Column('started_at', String),
             Column('finished_at', String), Column('status', String), Column('count', Integer))
errors = Table('crawl_errors', metadata, Column('id', Integer, primary_key=True),
               Column('run_id', ForeignKey('crawl_runs.id')), Column('reason', String))


def digest(body: bytes):
    return hashlib.sha256(body).hexdigest()


@contextmanager
def database(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(URL.create('sqlite', database=str(path)))

    @event.listens_for(engine, 'connect')
    def enable_foreign_keys(conn, _):
        conn.execute('PRAGMA foreign_keys=ON')

    try:
        metadata.create_all(engine)
        with engine.begin() as conn:
            yield conn
    finally:
        engine.dispose()


def register(conn, source, manual=None):
    chain_id = source.chain.casefold()
    conn.execute(insert(chains).values(id=chain_id, name=source.chain).on_conflict_do_nothing())
    conn.execute(insert(stores).values(id=source.id, chain_id=chain_id, name=source.store_name,
                                       address=source.store_address).on_conflict_do_nothing())
    source_id = f'{source.id}:{manual}' if manual else source.id
    config = {'mode': manual} if manual else source.model_dump(mode='json')
    conn.execute(insert(sources).values(id=source_id, store_id=source.id, config=config)
                 .on_conflict_do_update(index_elements=['id'], set_={'config': config}))
    return source_id


def persist(conn, source_id, content_hash, records, *, replay=False):
    base_key = f'{source_id}:{content_hash}'
    key = digest((f'{base_key}:{now().isoformat()}' if replay else base_key).encode())
    created = conn.execute(insert(artifacts).values(id=key, source_id=source_id,
                           sha256=content_hash, created_at=now().isoformat()).on_conflict_do_nothing())
    if not created.rowcount:
        return 0
    for row_number, record in enumerate(records):
        data = record.model_dump(mode='json') | {'evidence_id': key, 'raw_data_hash': content_hash}
        identity = {field: data[field] for field in ('jan_code', 'brand', 'product_name_normalized',
                                                      'origin_country', 'quantity', 'unit')}
        if record.jan_code:
            identity = {'jan_code': record.jan_code}
        else:
            identity['store_id'] = record.store_id
        product_id = digest(json.dumps(identity, sort_keys=True).encode())
        conn.execute(insert(products).values(id=product_id, identity=identity).on_conflict_do_nothing())
        conn.execute(insert(aliases).values(product_id=product_id, store_id=record.store_id,
                                            name=record.product_name_raw).on_conflict_do_nothing())
        conn.execute(prices.insert().values(id=str(record.id), store_id=record.store_id,
                     product_id=product_id, artifact_id=key, row_number=row_number,
                     record=data, observed_at=record.observed_at.isoformat()))
        if record.price_type == 'promotion':
            conn.execute(promotions.insert().values(observation_id=str(record.id),
                         valid_from=data['valid_from'], valid_until=data['valid_until']))
    return len(records)


def query(conn):
    rows = conn.execute(select(prices.c.record).order_by(prices.c.observed_at.desc()))
    return [PriceObservation.model_validate(data) for data in rows.scalars()]
