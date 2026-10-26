"""Adoption and cost, per skill and per person, from the audit log: what gets used, what gets
refused, how long approvals take, what the model costs, and which skills nobody opens."""
from __future__ import annotations

from datetime import timedelta


def report(con, skills: dict, days: int = 30) -> dict:
    con.execute("CREATE SCHEMA IF NOT EXISTS ops")
    since = f"now() - INTERVAL {int(days)} DAY"
    per_skill = con.execute(f"""
        SELECT skill, count(*) AS requests,
               count(*) FILTER (WHERE outcome = 'refused') AS refused,
               count(DISTINCT actor) AS people,
               round(median(latency_ms)) AS median_ms,
               sum(coalesce(input_tokens, 0)) AS input_tokens, sum(coalesce(output_tokens, 0)) AS output_tokens,
               round(sum(coalesce(cost, 0)), 4) AS cost
        FROM ops.audit WHERE logged_at >= {since} AND action IN ('answer', 'tool', 'propose') GROUP BY 1 ORDER BY 2 DESC""").df()
    props = con.execute(f"""
        SELECT status, count(*) AS n, median(date_diff('second', created_at, decided_at)) AS median_seconds_to_decide
        FROM ops.proposals WHERE created_at >= {since} GROUP BY 1""").df()
    used = set(per_skill.skill.dropna())
    # Hours retired: each successful use counts the minutes the skill's owner says the job takes by
    # hand (baseline_minutes in its YAML). The baseline is an estimate to re-measure, not a fact; the
    # count of successful uses is measured. Refusals and failed actions retire nothing.
    ok = con.execute(f"""SELECT skill, count(*) FROM ops.audit WHERE logged_at >= {since}
                         AND ((action = 'answer' AND outcome <> 'refused') OR (action = 'tool' AND outcome = 'ok')
                              OR (action = 'execute' AND outcome = 'executed'))
                         GROUP BY 1""").fetchall()
    hours = []
    for skill, n in ok:
        s = skills.get(skill)
        mins = getattr(s, "baseline_minutes", None) if s else None
        if mins:
            hours.append({"skill": skill, "successful_uses": int(n), "baseline_minutes": mins,
                          "hours_retired": round(n * mins / 60, 2), "baseline": getattr(s, "baseline_note", "")})
    return {"days": days, "by_skill": per_skill.to_dict("records"),
            "hours_retired": {"total": round(sum(h["hours_retired"] for h in hours), 2), "by_skill": hours,
                              "note": "successful uses are measured; minutes per use are each skill owner's baseline"},
            "proposals": props.to_dict("records"),
            "unused_skills": sorted(set(skills) - used),
            "people": int(con.execute(f"SELECT count(DISTINCT actor) FROM ops.audit WHERE logged_at >= {since}").fetchone()[0])}
