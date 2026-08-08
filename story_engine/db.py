"""
story_engine.db - the app's single data-access entry point. Every other
module (server.py, pipeline.py, character_studio.py, competition_engine.py,
certificates.py, payments.py, book_export.py, share_page.py) calls
story_engine.db.* and has never had to know or care what's actually storing
the data.

MySQL is now the ONLY supported backend - this file used to also contain a
full JSON-file-backed implementation (kertoons_data.json) selected at
runtime via a @_dispatch decorator, with mysql_store.py as an alternative
that could be switched to from the admin panel. That dual-backend design
is what caused a real production incident: a deploy could add a new table
to mysql_store.py's schema while the running server was still routing
through the JSON file, or the reverse (MySQL enabled, but a fresh table
added to the schema never got created because nothing re-ran
ensure_schema() automatically) - two independent implementations of the
same 20+ functions, only one of which was actually live at any moment, is
exactly the kind of surface that drifts out of sync. Removing the
alternative removes the whole class of bug, not just this one instance of
it.

This module is now a thin, explicit re-export of mysql_store.py's public
functions under this stable name, so no other file's `from . import db` /
`db.xxx(...)` call sites needed to change at all.
"""
from .mysql_store import (
    add_credits,
    add_footer_link,
    create_admin_if_missing,
    create_character,
    create_competition,
    create_coupon,
    create_or_update_entry,
    create_session,
    create_story_record,
    create_superadmin_if_missing,
    create_user,
    delete_character,
    delete_footer_link,
    delete_session,
    delete_story_record,
    delete_user,
    ensure_schema,
    finalize_competition,
    get_character,
    get_competition,
    get_cost_settings,
    get_entry,
    get_entry_by_id,
    get_image_credits,
    get_site_settings,
    get_story_owner_id,
    get_story_record,
    get_user_by_id,
    get_user_by_session_token,
    get_user_by_username,
    grant_credits_for_payment,
    increment_character_use_count,
    increment_character_view_count,
    increment_story_view_count,
    is_story_published,
    list_all_image_usage,
    list_all_payments,
    list_all_stories,
    list_all_users,
    list_characters_for_user,
    list_competitions,
    list_coupon_redemptions,
    list_coupons,
    list_entries_for_competition,
    list_entries_for_user,
    list_footer_links,
    list_image_usage_for_user,
    list_payments_for_user,
    list_pending_characters,
    list_published_characters,
    list_published_stories,
    list_stories_for_user,
    record_image_generation,
    redeem_coupon,
    set_character_reference_image,
    set_character_status,
    set_cost_settings,
    set_coupon_active,
    set_entry_certificates,
    set_entry_scores,
    set_site_settings,
    set_story_published,
    set_user_status,
    submit_character_for_publication,
    update_character,
    update_competition,
)
from . import backend_config

__all__ = [name for name in dir() if not name.startswith("_") and name not in ("backend_config",)]


def init_db():
    """Creates the database/every table if missing - idempotent, safe on
    every server startup. Kept under this name (rather than calling
    ensure_schema directly from server.py) purely so this module's own
    docstring/history stays the one place explaining why this now always
    talks to MySQL, no JSON fallback."""
    ensure_schema(backend_config.get_mysql_settings())
