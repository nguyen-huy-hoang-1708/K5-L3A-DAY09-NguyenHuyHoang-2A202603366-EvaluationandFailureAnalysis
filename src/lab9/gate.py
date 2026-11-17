"""CI gate: compare a PR eval report with the main-branch baseline using the metric spec.

    python -m lab9.gate --pr reports/eval_workflow.json --baseline reports/baseline_v1.json

exit 0 = merge, exit 1 = blocked. Safety metrics: max 0, no averaging away.
Capability metrics: meet the floor AND the paired CI of (PR - main) is not entirely below 0.
Thresholds may only get stricter on the same eval-set version (ratchet).
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from lab8.console import utf8_stdio
from lab9 import evalset, stats
from lab9.eval import SPEC, load_spec, per_item


SAFETY_SUM = ("secret_leak", "approval_bypass")   # aggregated as counts in lab9.eval.aggregate


def _bad_number(v) -> bool:
    return isinstance(v, float) and not math.isfinite(v)


def _pairs(rep: dict) -> list[tuple[str, int]]:
    return [(r.get("item_id"), r.get("seed")) for r in rep.get("rows") or []]


def _graded(spec: dict) -> list[str]:
    from lab9.graders import GRADERS
    return [m["id"] for m in spec.get("metrics") or [] if m["id"] in GRADERS]


def _recompute(rows: list[dict], metric: str, k_values: list[int]) -> tuple[bool, object]:
    """Recompute one aggregate from the rows (same formulas as lab9.eval.aggregate).
    Returns (known, value); known=False for metrics this function does not rebuild."""
    from lab9 import stats
    if metric in SAFETY_SUM or metric in (rows[0] if rows else {}):
        vals = [r.get(metric) for r in rows if r.get(metric) is not None]
        if metric in SAFETY_SUM:
            return True, sum(bool(v) for v in vals)
        return True, (round(sum(bool(v) for v in vals) / len(vals), 4) if vals else None)
    by_item: dict[str, list[bool]] = {}
    for r in rows:
        by_item.setdefault(r["item_id"], []).append(bool(r.get("pass_rate")))
    if metric == "pass_5":
        return True, (round(sum(all(v) for v in by_item.values()) / len(by_item), 4) if by_item else None)
    for name, fn in (("pass_at_", stats.pass_at_k), ("pass_hat_", stats.pass_hat_k)):
        if metric.startswith(name) and metric[len(name):].isdigit():
            k = int(metric[len(name):])
            if not by_item or min(len(v) for v in by_item.values()) < k:
                return True, None
            est = [fn(len(v), sum(v), k) for v in by_item.values()]
            return True, round(sum(est) / len(est), 4)
    if metric == "cost_per_success" and rows and all("cost" in r for r in rows):
        passes = sum(bool(r.get("pass_rate")) for r in rows)
        return True, (round(sum(r["cost"] for r in rows) / passes, 4) if passes else None)
    return False, None


def _ratchet(spec: dict, base_spec: dict) -> list[str]:
    """Thresholds on the same eval-set version may only get stricter."""
    # Only called once PR and baseline share an eval-set SHA (gate.check), so the eval set did NOT
    # change: a bumped eval_set_version here is a way to dodge the ratchet, not a new version.
    out: list[str] = []
    if spec.get("eval_set_version") != base_spec.get("eval_set_version"):
        out.append(f"ratchet: eval_set_version {base_spec.get('eval_set_version')!r} -> "
                   f"{spec.get('eval_set_version')!r} on an unchanged eval set")

    def cmp(where: str, old: dict, new: dict) -> None:
        th_o, th_n = old.get("threshold", old), new.get("threshold", new)
        for key in ("max", "max_increase"):
            if key in th_o and (key not in th_n or th_n[key] > th_o[key]):
                out.append(f"ratchet: {where} {key} loosened {th_o[key]} -> {th_n.get(key)}")
        if "min" in th_o and ("min" not in th_n or th_n["min"] < th_o["min"]):
            out.append(f"ratchet: {where} min loosened {th_o['min']} -> {th_n.get('min')}")
        if th_o.get("no_significant_drop") and not th_n.get("no_significant_drop"):
            out.append(f"ratchet: {where} dropped no_significant_drop")
        if old.get("blocking") and not new.get("blocking"):
            out.append(f"ratchet: {where} became non-blocking")
        if old.get("kind") == "safety" and new.get("kind") != "safety":
            out.append(f"ratchet: {where} no longer a safety metric")

    new_m = {m["id"]: m for m in spec.get("metrics") or []}
    for m in base_spec.get("metrics") or []:
        if m["id"] not in new_m:
            out.append(f"ratchet: metric {m['id']} removed")
        else:
            cmp(f"metric {m['id']}", m, new_m[m["id"]])
    key = lambda sl: (sl.get("name"), sl.get("metric", "pass_rate"))
    new_s = {key(sl): sl for sl in spec.get("slices") or []}
    for sl in base_spec.get("slices") or []:
        if key(sl) not in new_s:
            out.append(f"ratchet: slice {sl.get('name')}/{sl.get('metric', 'pass_rate')} removed")
        else:
            cmp(f"slice {sl.get('name')}/{sl.get('metric', 'pass_rate')}", sl, new_s[key(sl)])
    bo, bn = base_spec.get("bootstrap") or {}, spec.get("bootstrap") or {}
    for k in ("confidence", "n_resamples", "method"):
        if k in bo and bo.get(k) != bn.get(k):
            out.append(f"ratchet: bootstrap {k} changed {bo.get(k)} -> {bn.get(k)}")
    if set(base_spec.get("seeds") or []) - set(spec.get("seeds") or []):
        out.append("ratchet: seeds removed from the spec")
    for k in ("eval_set", "labels", "adversarial_pack"):
        if base_spec.get(k) != spec.get(k):
            out.append(f"ratchet: {k} changed on the same eval-set version")
    ro, rn = base_spec.get("reliable_items") or {}, spec.get("reliable_items") or {}
    if ro and (not rn or (ro.get("blocking") and not rn.get("blocking"))
               or ro.get("metric", "pass_rate") != rn.get("metric", "pass_rate")):
        out.append("ratchet: reliable_items check removed or loosened")
    if set(base_spec.get("pass_k") or []) - set(spec.get("pass_k") or []):
        out.append("ratchet: pass_k values removed")
    return out


def integrity_check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> list[str]:
    """M7 (Expert): refuse to compare reports that cannot be trusted. Return a list of problems
    (empty = trustworthy). A gate that passes a broken report is worse than no gate.
    Catches: NaN/inf metrics, dropped/duplicated rows, a (item, seed) set that differs from the
    baseline's, evaluator failures, None/NaN grader values, metrics that do not follow from the
    rows, a loosened spec (ratchet vs base_spec) and slice tags relabelled away from the baseline."""
    problems: list[str] = []
    for name, rep in (("PR", pr), ("baseline", base)):
        man = rep.get("manifest") or {}
        rows = rep.get("rows") or []
        for k, v in (rep.get("metrics") or {}).items():
            if _bad_number(v):
                problems.append(f"{name} metric {k} is {v}")
        if rep.get("evaluator_failures"):
            problems.append(f"{name} has {len(rep['evaluator_failures'])} evaluator failure(s): "
                            f"{rep['evaluator_failures'][0]}")
        want = man.get("items", 0) * len(man.get("seeds") or [])
        if len(rows) != want:
            problems.append(f"{name} has {len(rows)} rows, manifest says {man.get('items')} items x "
                            f"{len(man.get('seeds') or [])} seeds = {want}")
        pairs = _pairs(rep)
        dup = sorted({p for p in pairs if pairs.count(p) > 1}, key=str)
        if dup:
            problems.append(f"{name} has duplicated (item, seed) rows: {dup[:3]}")
        seeds = sorted({s for _, s in pairs}, key=str)
        if rows and seeds != sorted(man.get("seeds") or [], key=str):
            problems.append(f"{name} row seeds {seeds} differ from manifest seeds {man.get('seeds')}")
        if rows and len({i for i, _ in pairs}) != man.get("items"):
            problems.append(f"{name} rows cover {len({i for i, _ in pairs})} items, manifest says {man.get('items')}")
        for m in _graded(spec):
            bad = [f"{r.get('item_id')}@{r.get('seed')}" for r in rows
                   if m not in r or r[m] is None or _bad_number(r[m])]
            if bad:
                problems.append(f"{name} metric {m} has no value on {len(bad)} row(s) (crashed grader?): {bad[:3]}")
        if rows and not problems:
            k_values = list(spec.get("pass_k") or [])
            for k, v in (rep.get("metrics") or {}).items():
                known, again = _recompute(rows, k, k_values)
                if known and not (v == again or (isinstance(v, (int, float)) and isinstance(again, (int, float))
                                                 and abs(v - again) <= 1e-4)):
                    problems.append(f"{name} metric {k}={v} does not follow from its rows ({again})")
    pm, bm = pr.get("manifest") or {}, base.get("manifest") or {}
    if sorted(pm.get("seeds") or []) != sorted(bm.get("seeds") or []):
        problems.append(f"PR seeds {pm.get('seeds')} differ from baseline seeds {bm.get('seeds')}")
    if pm.get("items") != bm.get("items"):
        problems.append(f"PR covers {pm.get('items')} items, baseline {bm.get('items')}")
    pp, bp = set(_pairs(pr)), set(_pairs(base))
    if pp != bp:
        problems.append(f"(item, seed) set differs from the baseline: {len(bp - pp)} missing, {len(pp - bp)} extra")
    # Slice tags come from the eval set; PR and baseline must agree, and both must match the file.
    tags_pr = {r.get("item_id"): sorted(r.get("slices") or []) for r in pr.get("rows") or []}
    tags_base = {r.get("item_id"): sorted(r.get("slices") or []) for r in base.get("rows") or []}
    for item in sorted(set(tags_pr) & set(tags_base), key=str):
        if tags_pr[item] != tags_base[item]:
            problems.append(f"item {item} slice tags {tags_pr[item]} differ from baseline {tags_base[item]}")
    for rep_name, rep in (("PR", pr),):
        per_row: dict[str, set] = {}
        for r in rep.get("rows") or []:
            per_row.setdefault(r.get("item_id"), set()).add(tuple(sorted(r.get("slices") or [])))
        mixed = sorted(i for i, t in per_row.items() if len(t) > 1)
        if mixed:
            problems.append(f"{rep_name} item(s) {mixed[:3]} carry different slice tags across seeds")
    es = Path(spec["eval_set"]) if spec.get("eval_set") else None
    if es is not None and es.is_file() and pm.get("eval_set_sha") == evalset.sha12(es):
        truth = {it["item_id"]: sorted(it.get("slices") or []) for it in evalset.load_jsonl(es)}
        if set(tags_pr) != set(truth):
            problems.append(f"PR items differ from {es}: {len(set(truth) - set(tags_pr))} missing, "
                            f"{len(set(tags_pr) - set(truth))} unknown")
        if any("slices" in it for it in evalset.load_jsonl(es)):
            wrong = sorted(i for i in set(tags_pr) & set(truth) if tags_pr[i] != truth[i])
            if wrong:
                problems.append(f"slice tags of {wrong[:3]} differ from the eval set file")
    if base_spec is not None:
        problems += _ratchet(spec, base_spec)
    return problems


def slice_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """M5 (Advanced): an average can hide a regression on a small, important slice.
    For each entry of spec["slices"] ({"name", "metric", "min"?, "no_significant_drop"?, "blocking"}):
      - value = mean of per_item(pr, metric, slice_name=name)
      - fail if below "min", or if no_significant_drop and the paired CI over the slice's items
        lies entirely below 0
    Return (blocked ids like "slice:money_movement/pass_rate", report lines)."""
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for sl in spec.get("slices") or []:
        name, metric = sl["name"], sl.get("metric", "pass_rate")
        sid = f"slice:{name}/{metric}"
        a, b = per_item(pr, metric, slice_name=name), per_item(base, metric, slice_name=name)
        verdict, note, val = "ok", "", None
        if not a:
            verdict = "missing"                       # a slice that vanished cannot vouch for itself
        else:
            val = round(sum(a.values()) / len(a), 4)
            if "min" in sl and val < sl["min"]:
                verdict = "fail"
            if "max" in sl and val > sl["max"]:
                verdict = "fail"
            if sl.get("no_significant_drop"):
                keys = sorted(set(a) & set(b))
                if keys:
                    d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                                       n_resamples=bs.get("n_resamples", 1000),
                                                       rng=bs.get("rng", 2027),
                                                       confidence=bs.get("confidence", 0.95))
                    note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
                    if hi < 0:
                        verdict = "fail"
        if verdict != "ok" and sl.get("blocking"):
            blocked.append(sid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if sl.get("blocking") else "warn")
        th = {k: sl[k] for k in ("min", "max", "no_significant_drop") if k in sl}
        lines.append(f"  {sid:<34} n={len(a):<3} {val!s:<8} {json.dumps(th):<34} {note:<28} {label}")
    return blocked, lines


def reliable_items_check(pr: dict, base: dict, spec: dict) -> tuple[list[str], list[str]]:
    """Per-item pass^k ratchet: an item that passed on EVERY seed on main must still pass on every
    seed in the PR. Averages and pass^5 can stay flat when a PR fixes one flaky item and breaks
    another on a single seed; this check sees the newly unreliable item.
    Spec: reliable_items: {metric: pass_rate, blocking: true}."""
    cfg = spec.get("reliable_items")
    if not cfg:
        return [], []
    metric = cfg.get("metric", "pass_rate")
    a, b = per_item(pr, metric), per_item(base, metric)
    broke = sorted(k for k in set(a) & set(b) if b[k] >= 1.0 and a[k] < 1.0)
    rid = f"reliable_items/{metric}"
    label = ("BLOCK" if cfg.get("blocking") else "warn") if broke else "ok"
    lines = [f"  {rid:<34} {len(broke)} item(s) 5/5 on main now fail a seed {broke[:6]!s:<30} {label}"]
    return ([rid] if broke and cfg.get("blocking") else []), lines


def check(pr: dict, base: dict, spec: dict, base_spec: dict | None = None) -> tuple[list[str], list[str]]:
    if pr["manifest"]["eval_set_sha"] != base["manifest"]["eval_set_sha"]:
        return ["eval_set_sha"], ["  EVAL SET differs from the baseline's: re-run the baseline on the same eval set"]
    problems = integrity_check(pr, base, spec, base_spec)
    if problems:
        return ["integrity"], [f"  INTEGRITY {p}" for p in problems]
    blocked, lines = [], []
    bs = spec.get("bootstrap", {})
    for m in spec["metrics"]:
        mid, th, val = m["id"], m.get("threshold", {}), pr["metrics"].get(m["id"])
        verdict, note = "ok", ""
        if val is None:
            verdict = "missing"
        elif "max" in th and val > th["max"]:
            verdict = "fail"
        elif "min" in th and val < th["min"]:
            verdict = "fail"
        elif "max_increase" in th and base["metrics"].get(mid):
            if val > base["metrics"][mid] * (1 + th["max_increase"]):
                verdict = "fail"
        if th.get("no_significant_drop") and val is not None:
            a, b = per_item(pr, mid), per_item(base, mid)
            keys = sorted(set(a) & set(b))
            d, lo, hi = stats.paired_bootstrap([a[k] for k in keys], [b[k] for k in keys],
                                               n_resamples=bs.get("n_resamples", 1000), rng=bs.get("rng", 2027))
            note = f"Δ {d:+.3f} CI [{lo:+.3f}, {hi:+.3f}]"
            if hi < 0:
                verdict = "fail"
        if verdict != "ok" and m.get("blocking"):
            blocked.append(mid)
        label = {"ok": "ok", "missing": "MISSING"}.get(verdict, "BLOCK" if m.get("blocking") else "warn")
        lines.append(f"  {mid:<22} {val!s:<8} {json.dumps(th):<40} {note:<28} {label}")
    if spec.get("slices"):
        sb, sl = slice_check(pr, base, spec)
        blocked += sb
        lines += sl
    rb, rl = reliable_items_check(pr, base, spec)
    blocked += rb
    lines += rl
    return blocked, lines


def main(argv: list[str] | None = None) -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="lab9.gate")
    ap.add_argument("--pr", required=True)
    ap.add_argument("--baseline", required=True)
    ap.add_argument("--spec", default=str(SPEC))
    ap.add_argument("--base-spec", help="metric spec on main, for the ratchet check (M7)")
    args = ap.parse_args(argv)
    pr = json.loads(Path(args.pr).read_text(encoding="utf-8"))
    base = json.loads(Path(args.baseline).read_text(encoding="utf-8"))
    base_spec = load_spec(Path(args.base_spec)) if args.base_spec else None
    blocked, lines = check(pr, base, load_spec(Path(args.spec)), base_spec)
    print(f"gate: PR {pr['run_id']} vs main {base['run_id']} · eval set {pr['manifest']['eval_set_sha']}")
    print("\n".join(lines))
    if blocked:
        print(f"exit 1 — blocked: {', '.join(blocked)}")
        return 1
    print("exit 0 — merge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
