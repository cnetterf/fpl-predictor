"""Build FPLBench-compatible Free Hit squads and historical score artifacts.

The optimizer follows the public FPLBench methodology.  Forecasts always come
from this repository's immutable pre-deadline snapshots; completed outcomes and
deadline prices are read from the matching archived Official FPL gameweek file.
"""

from __future__ import annotations

import csv
import gzip
import io
import json
import math
import random
from collections import Counter
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parent
SNAPSHOT_MANIFEST = ROOT / "data" / "prediction_snapshots.json"
SNAPSHOT_DIR = ROOT / "data" / "prediction_snapshots"
OUTPUT_DIR = ROOT / "data" / "fplbench"
METHODOLOGY_URL = "https://www.fplbench.com/methodology"
POSITION_QUOTAS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
ROLE_MULTIPLIERS = {
    "captain": 2.0,
    "vice_captain": 1.0322,
    "starter": 1.0,
    "bench_goalkeeper": 0.0194,
    "bench_1": 0.2603,
    "bench_2": 0.0751,
    "bench_3": 0.0165,
}


def _read_gzip_json(path):
    return json.loads(gzip.decompress(Path(path).read_bytes()))


def _write_gzip_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), mtime=0))


def _archive_url(season, gameweek, entry):
    basis = entry.get("ownership_basis") or {}
    if int(basis.get("source_gameweek") or 0) == int(gameweek) and basis.get("source_url"):
        return basis["source_url"]
    long_season = f"{season[:5]}20{season[-2:]}" if len(season) == 7 else season
    return (
        "https://raw.githubusercontent.com/olbauday/FPL-Core-Insights/main/data/"
        f"{quote(long_season)}/By%20Gameweek/GW{gameweek}/playerstats.csv"
    )


def load_archived_players(season, gameweek, entry, loader=urlopen):
    """Load the archived deadline price, ownership, minutes and points."""
    url = _archive_url(season, gameweek, entry)
    with loader(url, timeout=30) as response:
        rows = csv.DictReader(io.TextIOWrapper(response, encoding="utf-8"))
        result = {}
        for row in rows:
            player_id = row.get("id")
            if not player_id:
                continue
            result[str(player_id)] = {
                "price": round(float(row.get("now_cost") or 0), 1),
                "ownership": float(row.get("selected_by_percent") or 0),
            }
    if len(result) < 100:
        raise RuntimeError(f"Archived GW{gameweek} player file contained only {len(result)} players")
    official_url = (
        "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data/"
        f"{season}/gws/gw{gameweek}.csv"
    )
    with loader(official_url, timeout=30) as response:
        rows = csv.DictReader(io.TextIOWrapper(response, encoding="utf-8"))
        official_rows = {
            str(row["element"]): {
                "minutes": int(float(row.get("minutes") or 0)),
                "actual_points": float(row.get("total_points") or 0),
            }
            for row in rows if row.get("element")
        }
    if len(official_rows) < 100:
        raise RuntimeError(f"Official historical GW{gameweek} file contained only {len(official_rows)} players")
    for player_id, history in result.items():
        history.update(official_rows.get(player_id, {"minutes": 0, "actual_points": 0}))
    return result, {"deadline_player_data": url, "official_results": official_url}


def unpack_players(snapshot, archived=None, bootstrap=None):
    archived = archived or {}
    current = {
        str(row.get("id")): row for row in (bootstrap or {}).get("elements", [])
        if row.get("id") is not None
    }
    players = []
    for row in snapshot.get("sources", {}).get("official", {}).get("players", []):
        player_id = str(row[0])
        history = archived.get(player_id, {})
        live = current.get(player_id, {})
        price = history.get("price")
        if price is None and len(row) > 8:
            price = row[8]
        if price is None:
            price = float(live.get("now_cost") or 0) / 10
        players.append({
            "id": int(row[0]),
            "name": row[1],
            "team": row[2],
            "position": row[3],
            "predicted_points": float(row[4] or 0),
            "expected_minutes": float(row[5] or 0),
            "ownership": float(history.get("ownership", row[6] or 0)),
            "price": round(float(price or 0), 1),
            "minutes": history.get("minutes"),
            "actual_points": history.get("actual_points"),
        })
    return [player for player in players if player["price"] > 0]


def _role_plan(squad):
    """Return the exact best XI, captaincy and ordered bench for a fixed squad."""
    by_position = {
        position: sorted((p for p in squad if p["position"] == position), key=lambda p: p["predicted_points"], reverse=True)
        for position in POSITION_QUOTAS
    }
    best = None
    for defenders in range(3, 6):
        for midfielders in range(2, 6):
            forwards = 10 - defenders - midfielders
            if not 1 <= forwards <= 3:
                continue
            starters = (
                by_position["GKP"][:1] + by_position["DEF"][:defenders]
                + by_position["MID"][:midfielders] + by_position["FWD"][:forwards]
            )
            starter_ids = {p["id"] for p in starters}
            ranked = sorted(starters, key=lambda p: p["predicted_points"], reverse=True)
            captain, vice = ranked[:2]
            bench_gk = next(p for p in by_position["GKP"] if p["id"] not in starter_ids)
            bench_outfield = sorted(
                (p for p in squad if p["position"] != "GKP" and p["id"] not in starter_ids),
                key=lambda p: p["predicted_points"], reverse=True,
            )
            objective = sum(p["predicted_points"] for p in starters)
            objective += captain["predicted_points"]
            objective += vice["predicted_points"] * (ROLE_MULTIPLIERS["vice_captain"] - 1)
            objective += bench_gk["predicted_points"] * ROLE_MULTIPLIERS["bench_goalkeeper"]
            objective += sum(
                player["predicted_points"] * ROLE_MULTIPLIERS[f"bench_{index}"]
                for index, player in enumerate(bench_outfield, 1)
            )
            if best is None or objective > best["objective"]:
                best = {
                    "objective": objective,
                    "starters": starters,
                    "captain": captain,
                    "vice_captain": vice,
                    "bench_goalkeeper": bench_gk,
                    "bench": bench_outfield,
                }
    return best


def validate_squad(squad):
    counts = Counter(p["position"] for p in squad)
    teams = Counter(p["team"] for p in squad)
    return (
        len(squad) == 15
        and counts == Counter(POSITION_QUOTAS)
        and sum(p["price"] for p in squad) <= 100.0001
        and max(teams.values(), default=0) <= 3
        and len({p["id"] for p in squad}) == 15
    )


def _cheap_legal_squad(players, score):
    selected = []
    teams = Counter()
    for position, quota in POSITION_QUOTAS.items():
        candidates = sorted(
            (p for p in players if p["position"] == position),
            key=lambda p: (score(p), p["predicted_points"], -p["price"]), reverse=True,
        )
        for player in candidates:
            if teams[player["team"]] >= 3:
                continue
            selected.append(player)
            teams[player["team"]] += 1
            if sum(1 for p in selected if p["position"] == position) == quota:
                break
    if len(selected) != 15:
        return None
    while sum(p["price"] for p in selected) > 100:
        best = None
        selected_ids = {p["id"] for p in selected}
        for outgoing in selected:
            for incoming in players:
                if incoming["id"] in selected_ids or incoming["position"] != outgoing["position"]:
                    continue
                saving = outgoing["price"] - incoming["price"]
                if saving <= 0:
                    continue
                changed = selected.copy()
                changed[changed.index(outgoing)] = incoming
                if max(Counter(p["team"] for p in changed).values()) > 3:
                    continue
                loss = max(0.001, outgoing["predicted_points"] - incoming["predicted_points"])
                candidate = (saving / loss, changed)
                if best is None or candidate[0] > best[0]:
                    best = candidate
        if best is None:
            return None
        selected = best[1]
    return selected


def optimize_squad(players):
    """Deterministic multi-start constraint optimization of the FPLBench objective."""
    eligible = [p for p in players if p["price"] > 0]
    seeds = []
    for price_penalty in (0, 0.08, 0.16, 0.24, 0.36, 0.5, 0.75, 1.0):
        seed = _cheap_legal_squad(
            eligible, lambda p, penalty=price_penalty: p["predicted_points"] - penalty * p["price"],
        )
        if seed:
            seeds.append(seed)
    rng = random.Random(202627)
    for _ in range(6):
        jitter = {p["id"]: rng.random() * 0.35 for p in eligible}
        seed = _cheap_legal_squad(
            eligible, lambda p, noise=jitter: p["predicted_points"] / max(p["price"], 0.1) + noise[p["id"]],
        )
        if seed:
            seeds.append(seed)
    if not seeds:
        raise RuntimeError("No legal £100m Free Hit squad could be constructed")

    # A player outside both the leading forecast and low-price sets cannot
    # improve a one-transfer local step. Keeping this deterministic candidate
    # frontier makes the twice-daily refresh fast without changing squad rules.
    search_pool = []
    for position in POSITION_QUOTAS:
        position_players = [p for p in eligible if p["position"] == position]
        selected_ids = {
            p["id"] for p in sorted(position_players, key=lambda p: p["predicted_points"], reverse=True)[:50]
        }
        selected_ids.update(p["id"] for p in sorted(position_players, key=lambda p: p["price"])[:20])
        search_pool.extend(p for p in position_players if p["id"] in selected_ids)

    best_squad = None
    best_plan = None
    for seed in seeds:
        squad = seed
        plan = _role_plan(squad)
        for _ in range(60):
            improvement = None
            ids = {p["id"] for p in squad}
            for index, outgoing in enumerate(squad):
                for incoming in search_pool:
                    if incoming["id"] in ids or incoming["position"] != outgoing["position"]:
                        continue
                    changed = squad.copy()
                    changed[index] = incoming
                    if not validate_squad(changed):
                        continue
                    changed_plan = _role_plan(changed)
                    if changed_plan["objective"] > plan["objective"] + 1e-9 and (
                        improvement is None or changed_plan["objective"] > improvement[0]
                    ):
                        improvement = (changed_plan["objective"], changed, changed_plan)
            if improvement is None:
                break
            _, squad, plan = improvement
        if best_plan is None or plan["objective"] > best_plan["objective"]:
            best_squad, best_plan = squad, plan
    return best_squad, best_plan


def score_actual(plan):
    if any(p["minutes"] is None for p in plan["starters"] + plan["bench"] + [plan["bench_goalkeeper"]]):
        return None
    starters = list(plan["starters"])
    bench = list(plan["bench"])
    goalkeeper = next(p for p in starters if p["position"] == "GKP")
    if goalkeeper["minutes"] == 0 and plan["bench_goalkeeper"]["minutes"] > 0:
        starters[starters.index(goalkeeper)] = plan["bench_goalkeeper"]
    for missing in [p for p in list(starters) if p["position"] != "GKP" and p["minutes"] == 0]:
        for replacement in bench:
            if replacement["minutes"] == 0:
                continue
            proposed = [p for p in starters if p["id"] != missing["id"]] + [replacement]
            counts = Counter(p["position"] for p in proposed)
            if counts["DEF"] >= 3 and counts["MID"] >= 2 and counts["FWD"] >= 1:
                starters = proposed
                bench = [p for p in bench if p["id"] != replacement["id"]]
                break
    points = sum(float(p["actual_points"] or 0) for p in starters)
    captain = plan["captain"]
    vice = plan["vice_captain"]
    if captain["minutes"] > 0:
        points += float(captain["actual_points"] or 0)
    elif vice["minutes"] > 0:
        points += float(vice["actual_points"] or 0)
    return points


def prediction_metrics(players):
    completed = [p for p in players if p["actual_points"] is not None]
    if not completed:
        return None
    owned = [p for p in completed if p["ownership"] >= 1]
    errors = [p["predicted_points"] - p["actual_points"] for p in owned]
    actual_top = sorted(completed, key=lambda p: p["actual_points"], reverse=True)[:15]
    predicted_top = sorted(completed, key=lambda p: p["predicted_points"], reverse=True)[:15]
    actual_ids = {p["id"] for p in actual_top}
    ideal = sorted((max(0, p["actual_points"]) for p in completed), reverse=True)[:15]
    relevance = {p["id"]: max(0, p["actual_points"]) for p in completed}
    dcg = sum((2 ** relevance[p["id"]] - 1) / math.log2(index + 2) for index, p in enumerate(predicted_top))
    idcg = sum((2 ** value - 1) / math.log2(index + 2) for index, value in enumerate(ideal))
    return {
        "scored_players": len(owned),
        "mae": round(sum(abs(error) for error in errors) / len(errors), 3) if errors else None,
        "rmse": round(math.sqrt(sum(error * error for error in errors) / len(errors)), 3) if errors else None,
        "ndcg_at_15": round(dcg / idcg, 4) if idcg else None,
        "top_15": len(actual_ids & {p["id"] for p in predicted_top}),
    }


def _squad_payload(squad, plan):
    starter_ids = {p["id"] for p in plan["starters"]}
    roles = {plan["captain"]["id"]: "captain", plan["vice_captain"]["id"]: "vice_captain"}
    roles[plan["bench_goalkeeper"]["id"]] = "bench_goalkeeper"
    roles.update({p["id"]: f"bench_{index}" for index, p in enumerate(plan["bench"], 1)})
    return [{
        "id": p["id"], "name": p["name"], "team": p["team"], "position": p["position"],
        "price": p["price"], "predicted_points": round(p["predicted_points"], 3),
        "actual_points": p["actual_points"], "minutes": p["minutes"],
        "role": roles.get(p["id"], "starter" if p["id"] in starter_ids else "bench"),
    } for p in squad]


def build_gameweek(season, gameweek, entry, bootstrap=None, loader=urlopen):
    snapshot_path = SNAPSHOT_DIR / entry["data_url"].removeprefix("./data/prediction_snapshots/")
    snapshot = _read_gzip_json(snapshot_path)
    archived = {}
    archive_url = None
    if entry.get("status") == "complete":
        archived, archive_url = load_archived_players(season, gameweek, entry, loader=loader)
    players = unpack_players(snapshot, archived=archived, bootstrap=bootstrap)
    squad, plan = optimize_squad(players)
    actual_score = score_actual(plan)
    return {
        "schema_version": 1,
        "season": season,
        "gameweek": int(gameweek),
        "status": entry.get("status", "pending"),
        "deadline_at": entry.get("deadline_at"),
        "captured_at": entry.get("captured_at"),
        "forecast_basis": entry.get("benchmark_policy", snapshot.get("benchmark_policy")),
        "methodology_url": METHODOLOGY_URL,
        "methodology": {"budget": 100.0, "club_limit": 3, "role_multipliers": ROLE_MULTIPLIERS},
        "archive_source": archive_url,
        "squad_price": round(sum(p["price"] for p in squad), 1),
        "optimization_score": round(plan["objective"], 3),
        "actual_squad_points": actual_score,
        "metrics": prediction_metrics(players),
        "squad": _squad_payload(squad, plan),
        "players": [{
            "id": p["id"], "name": p["name"], "team": p["team"], "position": p["position"],
            "price": p["price"], "ownership": p["ownership"],
            "predicted_points": round(p["predicted_points"], 3),
            "expected_minutes": p["expected_minutes"],
        } for p in players],
    }


def refresh_artifacts(bootstrap=None, force=False, loader=urlopen):
    manifest = json.loads(SNAPSHOT_MANIFEST.read_text())
    output = {"schema_version": 1, "methodology_url": METHODOLOGY_URL, "role_multipliers": ROLE_MULTIPLIERS, "seasons": {}}
    for season, season_payload in sorted(manifest.get("seasons", {}).items()):
        output_season = output["seasons"].setdefault(season, {"gameweeks": {}})
        for gameweek_text, entry in sorted(season_payload.get("gameweeks", {}).items(), key=lambda item: int(item[0])):
            gameweek = int(gameweek_text)
            relative = f"{season}/gw-{gameweek}.json.gz"
            target = OUTPUT_DIR / relative
            if force or not target.exists() or entry.get("status") != "complete":
                payload = build_gameweek(season, gameweek, entry, bootstrap=bootstrap, loader=loader)
                _write_gzip_json(target, payload)
            else:
                payload = _read_gzip_json(target)
            output_season["gameweeks"][gameweek_text] = {
                "status": payload["status"], "deadline_at": payload["deadline_at"],
                "captured_at": payload["captured_at"], "forecast_basis": payload["forecast_basis"],
                "data_url": f"./data/fplbench/{relative}",
                "squad_price": payload["squad_price"], "optimization_score": payload["optimization_score"],
                "actual_squad_points": payload["actual_squad_points"], "metrics": payload["metrics"],
            }
            if entry.get("status") == "pending":
                (OUTPUT_DIR / "latest.json").write_text(json.dumps(payload, separators=(",", ":")))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "manifest.json").write_text(json.dumps(output, separators=(",", ":")))
    return output
