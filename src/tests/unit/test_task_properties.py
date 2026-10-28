import json

from hypothesis import given, strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, rule

from api.validators import is_valid_location, normalize_submission, resolve_image_tag
from shared.constants import TASK_STATUS_SUBMITTED
from shared.responses import task_log_response
from shared.task_status import LEGAL_TRANSITIONS, apply_status_transition


VALID_TEXT = st.text(
    alphabet=st.characters(
        min_codepoint=48,
        max_codepoint=122,
        whitelist_categories=("Ll", "Lu", "Nd"),
    ),
    min_size=3,
    max_size=20,
)


@given(st.one_of(st.none(), VALID_TEXT))
def test_image_tag_defaulting_is_idempotent(image_tag):
    first = resolve_image_tag(image_tag)
    second = resolve_image_tag(first)
    assert first == second


@given(
    st.fixed_dictionaries(
        {
            "task_type": st.just("timeseries_forecast"),
            "training_prompt": VALID_TEXT.map(lambda value: f"  {value}  "),
            "data_location": VALID_TEXT.map(lambda value: f"  {value}/data.csv  "),
            "output_location": VALID_TEXT.map(lambda value: f"  {value}/output  "),
            "model_name": VALID_TEXT,
            "target_endpoint": VALID_TEXT,
            "submitted_by": VALID_TEXT,
            "confirmation_flag": st.sampled_from([True, "true", "1", "yes"]),
            "image_tag": st.one_of(st.none(), VALID_TEXT),
        }
    )
)
def test_payload_normalization_is_idempotent(payload):
    first = normalize_submission(payload)
    second = normalize_submission(first)
    assert first == second


@given(
    task_id=VALID_TEXT,
    status=st.sampled_from(sorted(LEGAL_TRANSITIONS)),
    logs=st.lists(st.dictionaries(st.text(min_size=1, max_size=5), st.integers()), max_size=5),
)
def test_task_log_response_preserves_shape(task_id, status, logs):
    response = task_log_response(task_id=task_id, status=status, logs=logs)
    body = json.loads(response["body"])
    assert response["statusCode"] == 200
    assert body["task_id"] == task_id
    assert body["status"] == status
    assert isinstance(body["logs"], list)


@given(
    prefix=st.sampled_from(["data/", "output/"]),
    value=VALID_TEXT,
)
def test_location_validation_rejects_parent_segments(prefix, value):
    assert not is_valid_location(f"{prefix}../{value}", allowed_prefixes=("data/", "output/", "s3://"))


class TaskStatusStateMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.task = {
            "task_id": "task-1",
            "status": TASK_STATUS_SUBMITTED,
            "updated_at": "2026-01-01T00:00:00Z",
        }

    @initialize()
    def start(self):
        self.task["status"] = TASK_STATUS_SUBMITTED

    @rule()
    def advance(self):
        current = self.task["status"]
        next_status = sorted(LEGAL_TRANSITIONS[current])[0]
        self.task = apply_status_transition(self.task, next_status, now="2026-01-01T00:01:00Z")
        assert self.task["status"] == next_status


TestTaskStatusStateMachine = TaskStatusStateMachine.TestCase
