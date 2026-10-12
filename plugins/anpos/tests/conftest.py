"""Keep approval-code pointers of the tests out of the real ~/.claude folder."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_pointer_home(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANPOS_POINTER_HOME", str(tmp_path_factory.mktemp("anpos-pointers")))
