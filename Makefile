# GhostBus task targets. Every target is mirrored in tasks.py (uv run python tasks.py <name>).
PY := uv run python
.PHONY: setup up down record recorder-status data replay sim train backtest eval test demo lint eval-live ablation langsmith mcp brief lock-prompts promptfoo load-test
setup: ; uv sync
up: ; $(PY) tasks.py up
down: ; $(PY) tasks.py down
record: ; $(PY) tasks.py record
recorder-status: ; $(PY) tasks.py recorder-status
data: ; $(PY) tasks.py data
replay: ; $(PY) tasks.py replay
sim: ; $(PY) tasks.py sim
train: ; $(PY) tasks.py train
backtest: ; $(PY) tasks.py backtest
eval: ; $(PY) tasks.py eval
test: ; $(PY) tasks.py test
demo: ; $(PY) tasks.py demo
lint: ; $(PY) tasks.py lint
eval-live: ; $(PY) tasks.py eval-live
ablation: ; $(PY) tasks.py ablation
langsmith: ; $(PY) tasks.py langsmith
mcp: ; $(PY) tasks.py mcp
brief: ; $(PY) tasks.py brief
lock-prompts: ; $(PY) tasks.py lock-prompts
promptfoo: ; $(PY) tasks.py promptfoo
load-test: ; $(PY) tasks.py load-test
