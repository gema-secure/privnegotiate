FROM python:3.12.14-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 依赖以 requirements.lock.txt 精确锁定（由 pip-compile 从 requirements.in 生成），
# Linux uvloop and Windows dependencies share one lock using platform markers.
COPY requirements.in requirements.lock.txt ./
RUN pip install --no-cache-dir -r requirements.lock.txt

# R11 验收：镜像内必须实证 CP-SAT 可求解，失败即构建中止
RUN python -c "from ortools.sat.python import cp_model; import ortools; m = cp_model.CpModel(); x = [m.NewBoolVar('x%d' % i) for i in range(3)]; m.Add(sum(x) == 2); m.Maximize(2 * x[0] + x[1] + x[2]); s = cp_model.CpSolver(); s.parameters.max_time_in_seconds = 5; r = s.Solve(m); assert r == cp_model.OPTIMAL, r; print('CP-SAT smoke OK', ortools.__version__)"

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
