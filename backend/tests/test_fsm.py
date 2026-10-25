from app.orchestration.state_machine import InvalidTransition, TaskState, transition


def test_normal_lifecycle():
    s = TaskState.planning
    for nxt in (TaskState.pending_confirm, TaskState.running, TaskState.reviewing, TaskState.done):
        s = transition(s, nxt)
    assert s is TaskState.done


def test_reject_lifecycle():
    s = transition(TaskState.planning, TaskState.pending_confirm)
    assert transition(s, TaskState.cancelled) is TaskState.cancelled


def test_review_can_go_back_to_running():
    """M3 自动重试回路。"""
    s = transition(TaskState.pending_confirm, TaskState.running)
    s = transition(s, TaskState.reviewing)
    assert transition(s, TaskState.running) is TaskState.running


def test_terminal_states_are_final():
    for terminal in (TaskState.done, TaskState.failed, TaskState.cancelled):
        try:
            transition(terminal, TaskState.running)
        except InvalidTransition:
            pass
        else:
            raise AssertionError(f"{terminal} 应为终态")
