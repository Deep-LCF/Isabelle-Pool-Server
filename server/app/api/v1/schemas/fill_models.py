"""Request model of the server-side ``fill_sorries`` job (plan 2026-10-18 §3.1).
Its own module: ``API_models.py`` sits at the source-size gate."""

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from server.app.core.config import Memory
from server.app.core.input_guards import reject_code_execution


class FillSorriesRequest(BaseModel):  # pylint: disable=too-few-public-methods
    """Submit a proof with ``sorry``s for the server-side sledgehammer job
    (plan 2026-10-18 §3.1)."""

    text: str = Field(
        description="The proof text; every `sorry` command becomes a target."
    )
    mode: Literal["document", "chunk"] = Field(
        default="document",
        description="document: load the text as the whole document (file-sync clients); "
        "chunk: append it as a chunk (verify_chunk semantics).",
    )
    timeout_s: int = Field(
        default=Memory.FILL_TIMEOUT_S,
        ge=1,
        le=300,
        description="Isabelle sledgehammer budget per subgoal (1–300 s).",
    )
    max_total_s: float = Field(
        default=Memory.FILL_MAX_TOTAL_S,
        ge=1.0,
        le=3600.0,
        description="Wall bound of the whole job, including time parked.",
    )
    load_timeout: float = Field(
        default=120.0, ge=1.0, description="Budget for loading/verifying the text."
    )
    provers: Optional[str] = Field(
        default=None,
        pattern=r"^[a-z0-9_]+( [a-z0-9_]+)*$",
        description="Optional space-separated prover names (Isabelle default when omitted).",
    )
    # staged fill (2026-11-10): cheap closers first, a verdict per failed sorry,
    # a structured retry, mechanical splitting, and parallel sessions
    try0_s: int = Field(default=5, ge=0, le=60,
                        description="try0 (simp/auto/blast/linarith/...) budget per method before sledgehammer; 0 = off.")
    nitpick_s: int = Field(default=10, ge=0, le=120,
                           description="nitpick budget on a sorry try0 could not close; a genuine counterexample marks it `false`; 0 = off.")
    isar: bool = Field(default=True,
                       description="Retry once with isar_proofs=true when a prover found a proof the one-liner could not reconstruct.")
    split: bool = Field(default=True,
                        description="Split a failed sorry whose conclusion is a conjunction/universal/implication/iff/bounded-universal "
                                    "into `intro` leaves and attack them in a second pass.")
    parallel: int = Field(default=1, ge=1, le=8,
                          description="Sessions working the sorries concurrently (siblings with the same imports; bounded by the sledgehammer slots).")

    @field_validator("text")
    @classmethod
    def _no_code_execution(cls, v: str) -> str:
        # routes/fill.py hands `text` to the session itself (load_document /
        # verify_chunk), so the validators of the other text models never see
        # it: with the guard on, this route still accepted ML and
        # axiomatization until 2026-10-27 (report recheck 3B.2).
        return reject_code_execution(v, what="fill text")
