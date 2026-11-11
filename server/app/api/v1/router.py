"""v1 API aggregate router + compatibility facade.

The endpoints live in ``server/app/api/v1/routes/`` (one module per concern);
this module composes them in registration order (route matching is ordered)
and re-exports the names that external code and older tests imported from
here. New code should import from ``routes.<module>``, ``deps`` and
``serializers`` directly; the re-exports below are kept for one release.
"""
from fastapi import APIRouter

from .routes import (
    automation,
    checkpoints,
    execution,
    fill,
    health,
    heaps,
    inspection,
    memory,
    positional,
    sessions,
    symbols,
)

router = APIRouter()
for _module in (
    health,
    sessions,
    execution,
    inspection,
    positional,
    automation,
    checkpoints,
    heaps,
    memory,
    fill,
    symbols,
):
    router.include_router(_module.router)

# --- compatibility re-exports (deprecated import paths) ----------------------
from .deps import (  # noqa: E402
    require_admin_token as _require_admin_token,
    require_lease_id as _require_lease_id,
    safe_segment as _safe_segment,
)
from .serializers import (  # noqa: E402
    heap_entry_response as _heap_entry_response,
    parse_command_range as _parse_command_range,
    parse_located_command as _parse_located_command,
    preview as _preview,
    to_ascii as _ascii,
)
from .routes.checkpoints import restore_checkpoint, rollback, save_checkpoint  # noqa: E402
from .routes.execution import (  # noqa: E402
    enter_theory,
    execute_big_step,
    execute_command,
    get_last_report,
    load_document,
    parse_theory_header_endpoint,
    verify_chunk,
)
from .routes.health import healthz, readyz, root  # noqa: E402
from .routes.heaps import (  # noqa: E402
    build_heap,
    delete_heap,
    delete_heap_group,
    delete_heap_image,
    get_heap_manifest,
    list_available_heaps,
    list_heap_groups,
    list_heaps,
)
from .routes.inspection import (  # noqa: E402
    get_global_facts,
    get_local_facts,
    get_proof_state,
    get_source,
    get_subgoals,
    run_diagnostic,
)
from .routes.positional import (  # noqa: E402
    command_at_line,
    definition_at,
    goals_at_line,
    hover_at,
    sledgehammer_at,
)
from .routes.sessions import (  # noqa: E402
    acquire_session,
    close_session,
    create_session,
    get_command_history,
    get_session_info,
    get_session_stats,
    list_sessions,
    list_sessions_admin,
    release_session,
)
from .routes.automation import sledgehammer  # noqa: E402

__all__ = ["router"]
