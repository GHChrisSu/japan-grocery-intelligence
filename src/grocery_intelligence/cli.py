import csv
import io
import json
from pathlib import Path
from typing import Annotated
from urllib.error import URLError

import typer
import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from sqlalchemy import func, select

from grocery_intelligence.config import Settings, sources
from grocery_intelligence.crawl import collect
from grocery_intelligence.database import (
    database,
    digest,
    persist,
    query,
    register,
    runs,
)
from grocery_intelligence.documents import MAX_BYTES, document_text, parse_document
from grocery_intelligence.models import (
    JST,
    PriceObservation,
    normalized,
    now,
    unit_price,
)
from grocery_intelligence.nearby import nearby_sources
from grocery_intelligence.parsing import has_layout
from grocery_intelligence.vision import analyze_flyer, read_review

app = typer.Typer(no_args_is_help=True)
StoreOpt = Annotated[str, typer.Option("--store", help="Store ID from discover")]


def catalog():
    return sources(Settings().source_config)


def store_by_id(store):
    for source in catalog():
        if source.id == store:
            return source
    raise typer.BadParameter(f"Unknown store ID {store!r}")


@app.command()
def discover():
    """Show the local, reviewed source catalog without contacting stores."""
    candidates = sorted(
        catalog(),
        key=lambda item: (
            item.scenario_distance_km is None,
            item.scenario_distance_km or 0,
        ),
    )
    for source in candidates:
        distance = (
            f"{source.scenario_distance_km:.2f} km"
            if source.scenario_distance_km is not None
            else "–"
        )
        typer.echo(
            f"{distance}\t{source.id}\t{source.store_name}\t{source.access_status}\t{source.url}"
        )


@app.command()
def nearby(
    latitude: float,
    longitude: float,
    output: Annotated[Path, typer.Option("--output")],
    radius: int = 1500,
):
    """Write disabled OpenStreetMap store candidates to a new YAML file."""
    if output.exists():
        raise typer.BadParameter("Choose a new output path")
    try:
        candidates = nearby_sources(latitude, longitude, radius, Settings().user_agent)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("x", encoding="utf-8") as stream:
            yaml.safe_dump(candidates, stream, allow_unicode=True, sort_keys=False)
    except (URLError, OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from None
    typer.echo(f"Wrote {len(candidates)} disabled store candidates to {output}")


@app.command()
def check_sources():
    items = catalog()
    for source in items:
        if source.enabled and not has_layout(source):
            raise typer.BadParameter(f"Unknown parser: {source.parser}")
    typer.echo(
        f"{len(items)} configured, {sum(source.enabled for source in items)} enabled"
    )


def run(source, settings):
    status, count = collect(source, settings)
    typer.echo(f"{source.id}\t{status}\t{count}")
    return status


@app.command()
def crawl(
    store: StoreOpt | None = None,
    all_sources: Annotated[bool, typer.Option("--all")] = False,
):
    if bool(store) == all_sources:
        raise typer.BadParameter("Choose --store or --all")
    selected = (
        [store_by_id(store)]
        if store
        else [source for source in catalog() if source.enabled]
    )
    if not selected:
        typer.echo("No enabled sources")
    settings = Settings()
    outcomes = [run(source, settings) for source in selected]
    if any(status in ("error", "disabled", "unknown-parser") for status in outcomes):
        raise typer.Exit(1)


@app.command()
def analyze(
    path: Path, store: StoreOpt, output: Annotated[Path, typer.Option("--output")]
):
    """Send a local flyer image to your configured model and write a review draft."""
    source = store_by_id(store)
    if output.exists():
        raise typer.BadParameter("Choose a new draft path")
    try:
        draft = analyze_flyer(path, source, Settings())
        output.write_text(draft.model_dump_json(indent=2) + "\n", encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from None
    typer.echo(f"Wrote {len(draft.items)} draft rows to {output}")


@app.command()
def import_review(path: Path, store: StoreOpt):
    """Import an edited draft after reviewing it against its flyer."""
    source = store_by_id(store)
    try:
        body = path.read_bytes()
        records = read_review(body, source)
        with database(Settings().database) as conn:
            count = persist(
                conn, register(conn, source, "flyer"), digest(body), records
            )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from None
    typer.echo(f"Stored {count} reviewed observations")


def import_document(path, store, kind, accept_reviewed):
    source = store_by_id(store)
    try:
        with path.open("rb") as stream:
            body = stream.read(MAX_BYTES + 1)
        draft = parse_document(document_text(path, body), source, kind)
        if accept_reviewed:
            with database(Settings().database) as conn:
                count = persist(conn, register(conn, source, kind), digest(body), draft)
            typer.echo(f"Stored {count} observations")
        else:
            typer.echo(
                json.dumps(
                    [row.model_dump(mode="json") for row in draft],
                    ensure_ascii=False,
                    indent=2,
                )
            )
    except (ValueError, RuntimeError, OSError) as exc:
        raise typer.BadParameter(str(exc)) from None


@app.command()
def import_flyer(path: Path, store: StoreOpt, accept_reviewed: bool = False):
    """Preview a locally supplied flyer. Save with --accept-reviewed."""
    import_document(path, store, "flyer", accept_reviewed)


@app.command()
def import_receipt(path: Path, store: StoreOpt, accept_reviewed: bool = False):
    """Preview a locally supplied receipt. Save with --accept-reviewed."""
    import_document(path, store, "receipt", accept_reviewed)


def matches(term=""):
    token = normalized(term).casefold()
    with database(Settings().database) as conn:
        return [
            row
            for row in query(conn)
            if token in row.product_name_normalized.casefold()
        ]


def json_rows(rows):
    return json.dumps(
        [row.model_dump(mode="json") for row in rows], ensure_ascii=False, indent=2
    )


@app.command()
def search(term: str):
    typer.echo(json_rows(matches(term)))


@app.command()
def history(term: str):
    typer.echo(json_rows(matches(term)))


@app.command()
def compare(term: str):
    """Group known SKU and price bases. Receipt and unknown-channel data stay in history."""
    current = now()
    latest = {}
    for row in matches(term):
        if (
            row.sales_channel == "unknown"
            or row.price_type == "receipt"
            or not row.jan_code
        ):
            continue
        if (row.valid_from and current < row.valid_from) or (
            row.valid_until and current >= row.valid_until
        ):
            continue
        base = (
            row.jan_code,
            row.sales_channel,
            row.membership_required,
            row.price_type,
            row.purchase_limit,
        )
        for basis in ("including", "excluding"):
            price = getattr(row, f"price_{basis}_tax")
            if price is not None:
                per_unit = unit_price(row, basis)
                amount, unit = per_unit or (price, "SKU")
                key = (*base, basis, unit)
                latest.setdefault((key, row.store_id), (amount, row))
    result = []
    for (key, _), (price, row) in sorted(
        latest.items(), key=lambda item: (repr(item[0][0]), item[1][0])
    ):
        result.append(
            {
                "group": key,
                "price": str(price),
                "observation": row.model_dump(mode="json"),
            }
        )
    typer.echo(json.dumps(result, ensure_ascii=False, indent=2))


@app.command()
def export(format: str = "json"):
    rows = matches()
    if format == "json":
        typer.echo(json_rows(rows))
    elif format == "csv":
        out = io.StringIO()
        writer = csv.DictWriter(
            out, list(PriceObservation.model_fields), lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(row.model_dump(mode="json") for row in rows)
        typer.echo(out.getvalue(), nl=False)
    else:
        raise typer.BadParameter("Use json or csv")


def build_scheduler(settings):
    service = BlockingScheduler(
        timezone=JST,
        job_defaults={"max_instances": 1, "coalesce": True},
        executors={"default": {"type": "threadpool", "max_workers": 1}},
    )
    for item in sources(settings.source_config):
        if item.enabled:
            service.add_job(
                run,
                "interval",
                hours=item.crawl_interval_hours,
                args=(item, settings),
                id=item.id,
                next_run_time=now(),
            )
    return service


@app.command()
def scheduler():
    service = build_scheduler(Settings())
    if not service.get_jobs():
        typer.echo("No enabled sources. Scheduler did not start")
        return
    service.start()


@app.command()
def report():
    with database(Settings().database) as conn:
        rows = conn.execute(
            select(runs.c.status, func.count()).group_by(runs.c.status)
        ).all()
        observations = query(conn)
    typer.echo(
        json.dumps(
            {
                "run_counts": dict(rows),
                "observations": len(observations),
                "retail_web_observations": sum(
                    row.source_url is not None for row in observations
                ),
                "seven_day_review": "Pending manual evidence review",
            }
        )
    )
