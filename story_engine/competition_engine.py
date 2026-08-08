"""
Closes a competition: entries are already scored as they're submitted (see
server.py's POST /api/competitions/enter, which scores immediately via
openai_client.score_competition_entry) - this module's only job is ranking
and certificate generation, run once an admin clicks "Close & Finalize".
"""
import json
import os

from . import certificates, config, db


def finalize(competition_id: int) -> dict:
    """Ranks every scored entry (score_total desc, tie-broken by earliest
    submission), marks the top 3 as winners, generates/overwrites every
    entry's certificate(s), and closes the competition. Always recomputes
    from scratch rather than checking a "already finalized" guard - safe to
    call more than once for the same competition; re-running with the same
    entries/scores produces the identical result every time. Raises
    ValueError if the competition doesn't exist."""
    competition = db.get_competition(competition_id)
    if not competition:
        raise ValueError(f"unknown competition_id {competition_id}")

    entries = db.list_entries_for_competition(competition_id)
    scored = [e for e in entries if e.get("score_total") is not None]
    # Entries that somehow never got scored (e.g. a scoring call failed and
    # was never retried) rank last, in submission order, rather than being
    # silently dropped from the results entirely.
    unscored = [e for e in entries if e.get("score_total") is None]
    scored.sort(key=lambda e: (-float(e["score_total"]), e["submitted_at"]))
    unscored.sort(key=lambda e: e["submitted_at"])
    ordered = scored + unscored

    ranked_tuples = []
    for i, entry in enumerate(ordered):
        rank = i + 1
        is_winner = rank <= 3 and entry in scored  # never award a podium spot to an unscored entry
        ranked_tuples.append((entry["id"], rank, is_winner))

    db.finalize_competition(competition_id, ranked_tuples)
    competition = db.get_competition(competition_id)  # re-read: now status="closed"

    for entry_id, rank, is_winner in ranked_tuples:
        _generate_certificates_for_entry(entry_id, rank, is_winner, competition)

    return competition


def _generate_certificates_for_entry(entry_id: int, rank: int, is_winner: bool, competition: dict):
    entry = db.get_entry_by_id(entry_id)
    if not entry:
        return
    story_path = os.path.join(config.GENERATED_DIR, entry["job_id"], "story.json")
    if not os.path.exists(story_path):
        # The story was deleted after entering but before finalize ran -
        # rank/is_winner are still recorded, just no certificate PDF to show.
        return
    with open(story_path, "r", encoding="utf-8") as f:
        story = json.load(f)

    participation_bytes = certificates.generate_participation_certificate(entry, story, competition)
    participation_path = f"certificates/{competition['id']}/{entry_id}_participation.pdf"
    _write_certificate(participation_path, participation_bytes)

    winner_path = None
    if is_winner:
        winner_bytes = certificates.generate_winner_certificate(entry, story, competition, rank)
        winner_path = f"certificates/{competition['id']}/{entry_id}_winner.pdf"
        _write_certificate(winner_path, winner_bytes)

    db.set_entry_certificates(entry_id, participation_path, winner_path)


def _write_certificate(relpath: str, data: bytes):
    full_path = os.path.join(config.GENERATED_DIR, relpath)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "wb") as f:
        f.write(data)
