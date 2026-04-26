from __future__ import annotations

from pathlib import Path

from energy_assistant.ems.models import EmsPlanOutput
from energy_assistant.plotting.plan import write_plan_image, write_plan_svg


def _build_fixture_plan() -> EmsPlanOutput:
    return EmsPlanOutput.model_validate_json(
        Path("tests/fixtures/ems/nwhass/short-horizon-low-pv/output.json").read_text()
    )


def test_write_plan_svg_outputs_svg_document(tmp_path: Path) -> None:
    output = tmp_path / "plan.svg"

    write_plan_svg(_build_fixture_plan(), output)

    rendered = output.read_text()
    assert rendered.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    assert "<svg " in rendered
    assert "EMS Plan" in rendered
    assert "<script" not in rendered


def test_write_plan_image_wrapper_delegates_to_svg_renderer(tmp_path: Path) -> None:
    output = tmp_path / "plan.svg"

    write_plan_image(_build_fixture_plan(), output)

    assert output.exists()
    assert "<svg " in output.read_text()
