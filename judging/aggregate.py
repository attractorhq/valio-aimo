"""Aggregate blind-judge transcripts into one report.

usage: python3 -I judging/aggregate.py <runs-dir> <report-base> <url> <model> <selftest 0|1>
Reads <runs-dir>/<persona>/transcript.jsonl (Claude stream-json), extracts each judge's final JSON
block, and writes <report-base>.md and <report-base>.json. Standard library only.
"""

import json
import re
import sys
from pathlib import Path

WEIGHTS = {"demo": 30, "need": 20, "controls": 20, "robustness": 15, "openness": 15}
NAMES = {
    "demo": "Working end-to-end demo",
    "need": "Solving the problem and user need",
    "controls": "Autonomy and controls",
    "robustness": "Robustness to exception situations",
    "openness": "Openness and path to production",
}


def final_text(transcript: Path) -> tuple[str, dict]:
    result, meta = "", {}
    if not transcript.exists():
        return "", {"error": "no transcript"}
    for line in transcript.read_text(errors="replace").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "result":
            result = event.get("result") or ""
            meta = {k: event.get(k) for k in ("subtype", "num_turns", "total_cost_usd", "duration_ms", "is_error")}
    return result, meta


def last_json_block(text: str) -> dict | None:
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, flags=re.S)
    for block in reversed(blocks):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    return None


def weighted(scores: dict) -> float:
    return round(sum(float(scores.get(k, {}).get("score", 0)) / 10 * w for k, w in WEIGHTS.items()), 1)


def main() -> None:
    runs, base, url, model, selftest = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4], sys.argv[5] == "1"
    judges = []
    for d in sorted(p for p in runs.iterdir() if p.is_dir()):
        text, meta = final_text(d / "transcript.jsonl")
        data = last_json_block(text)
        judges.append({"persona": d.name, "meta": meta, "data": data, "raw": text if data is None else None})

    if selftest:
        for j in judges:
            print(f"selftest ({j['persona']}): {json.dumps(j['data'] or {'raw': (j['raw'] or '')[-1500:]}, indent=1)}")
            print(f"  meta: {j['meta']}")
        ok = all(j["data"] and j["data"].get("repo_readable") is False and j["data"].get("foreign_context") == "none" for j in judges)
        print("selftest:", "PASS (judge is isolated)" if ok else "CHECK MANUALLY — see transcript in", runs)
        return

    valid = [j for j in judges if j["data"] and "scores" in j["data"]]
    out = [f"# Blind judge report — {base.name}", "", f"App: {url} · model: {model} · judges: {len(valid)}/{len(judges)}", ""]
    if not valid:
        out += ["**No valid judge output.** See transcripts in `judging/runs/`.", ""]
    else:
        out += ["| Criterion | Weight | " + " | ".join(j["persona"] for j in valid) + " | Mean |",
                "|---|---|" + "---|" * len(valid) + "---|"]
        means = {}
        for k, w in WEIGHTS.items():
            vals = [float(j["data"]["scores"].get(k, {}).get("score", 0)) for j in valid]
            means[k] = sum(vals) / len(vals)
            out.append(f"| {NAMES[k]} | {w}% | " + " | ".join(f"{v:g}" for v in vals) + f" | **{means[k]:.1f}** |")
        totals = [weighted(j["data"]["scores"]) for j in valid]
        total = round(sum(means[k] / 10 * w for k, w in WEIGHTS.items()), 1)
        out += [f"| **Weighted total /100** | | " + " | ".join(f"{t:g}" for t in totals) + f" | **{total}** |", ""]

        out += ["## Verdicts", ""] + [f"- **{j['persona']}**: {j['data'].get('one_line_verdict', '')}" for j in valid] + [""]

        imps = [dict(i, persona=j["persona"]) for j in valid for i in j["data"].get("improvements", [])]
        imps.sort(key=lambda i: -float(i.get("expected_gain_points") or 0))
        out += ["## Improvements (ranked by expected gain)", "", "| Gain | Effort | Criterion | Improvement | Why (observed) | Judge |", "|---|---|---|---|---|---|"]
        out += [f"| {i.get('expected_gain_points', '')} | {i.get('effort', '')} | {i.get('criterion', '')} | {i.get('title', '')} | {i.get('why', '')} | {i['persona']} |" for i in imps]
        out.append("")

        bugs = [dict(b, persona=j["persona"]) for j in valid for b in j["data"].get("bugs", [])]
        if bugs:
            out += ["## Bugs seen by judges", ""]
            out += [f"- **[{b.get('severity', '?')}] {b.get('title', '')}** ({b['persona']}): {b.get('steps', '')} → expected: {b.get('expected', '')}; actual: {b.get('actual', '')}" for b in bugs]
            out.append("")

        out += ["## Strengths", ""] + [f"- {s} ({j['persona']})" for j in valid for s in j["data"].get("strengths", [])] + [""]
        out += ["## Requests the judges tried", "", "| Judge | Request | Outcome | Verdict |", "|---|---|---|---|"]
        out += [f"| {j['persona']} | {r.get('request', '')} | {r.get('outcome', '')} | {r.get('verdict', '')} |" for j in valid for r in j["data"].get("requests_tried", [])]
        out.append("")
        unver = [f"- {c} ({j['persona']})" for j in valid for c in j["data"].get("claims_not_verified", [])]
        if unver:
            out += ["## Submission claims the judges could not verify", ""] + unver + [""]
        out += ["## Per-criterion evidence", ""]
        for k in WEIGHTS:
            out.append(f"### {NAMES[k]}")
            for j in valid:
                s = j["data"]["scores"].get(k, {})
                out.append(f"- **{j['persona']} ({s.get('score', '?')})**: " + "; ".join(s.get("evidence", [])) + (" — weak: " + "; ".join(s.get("weaknesses", [])) if s.get("weaknesses") else ""))
            out.append("")
    for j in judges:
        if j["data"] is None:
            out += [f"## {j['persona']}: no JSON verdict", "", "```", (j["raw"] or "(empty)")[-3000:], "```", ""]
    cost = sum(float(j["meta"].get("total_cost_usd") or 0) for j in judges)
    out.append(f"_Cost ≈ ${cost:.2f} · turns: " + ", ".join(f"{j['persona']}={j['meta'].get('num_turns')}" for j in judges) + "_")

    base.with_suffix(".md").write_text("\n".join(out) + "\n")
    base.with_suffix(".json").write_text(json.dumps({"url": url, "model": model, "judges": [{"persona": j["persona"], "meta": j["meta"], "verdict": j["data"]} for j in judges]}, indent=1, ensure_ascii=False))
    summary = out[4:12] if valid else out[:6]
    print("\n".join(summary))
    print(f"\njudge: report → {base.with_suffix('.md')}")


if __name__ == "__main__":
    main()
