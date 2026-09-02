"""ChaseBit: the reference cross-fixture effect, written to the TEST Room
spec (fixtures "main" and "accent"). Split out of TestBit so TestBit itself
stays loadable on any Room it declares support for (including DEMO, whose
one "array" fixture chase does not name) -- the load-time fixture contract
(control/engine.py's load_bit) refuses a Bit that addresses a fixture its
active Room does not declare, by design, so a Bit naming fixtures belongs to
the Room spec it was written against.
"""

from control.cues import fixture_dev
from control.functions import Condition, ConditionSource, FunctionTarget, ScriptStep
from control.functions import Function
from bits.test.test_bit import TestBit


class ChaseBit(TestBit):
    room_types = {"TEST"}

    @property
    def function_table(self):
        table = super().function_table
        table.functions["chase"] = Function(
            name="chase",
            description="Steps a hue flash from main to accent, then clears "
                        "both: the reference cross-fixture effect, addressed "
                        "by fixture name against the TEST Room spec",
            target=FunctionTarget.ROOM,
            condition=Condition(
                name="operator_chase", description="Operator fires it",
                source=ConditionSource.ADMIN_MANUAL),
            script=(
                ScriptStep(0.0, (fixture_dev("main"), 0xB0, 74, 127)),
                ScriptStep(0.5, (fixture_dev("accent"), 0xB0, 74, 127)),
                ScriptStep(1.0, (fixture_dev("main"), 0xB0, 74, 0)),
                ScriptStep(1.0, (fixture_dev("accent"), 0xB0, 74, 0)),
            ),
        )
        return table
