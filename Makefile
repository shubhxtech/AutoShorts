# AutoShorts – common commands
PY := backend/.venv/bin/python
CF := backend/.venv/bin/autoshorts

.PHONY: setup up dev web mac demo test clean

setup:            ## create the Python env, install everything, build the dashboard
	cd backend && (test -d .venv || python3 -m venv .venv) && .venv/bin/pip install -e ".[all,dev]"
	cd frontend && npm install --no-audit --no-fund && npm run build

up: web           ## start dashboard + worker + scheduler on http://localhost:3000
	$(CF) up

dev:              ## backend on :3000 and hot-reloading dashboard on :5173
	$(CF) up & cd frontend && npm run dev

web:              ## build the dashboard
	cd frontend && npm run build

mac:              ## build dist/AutoShorts.app and dist/AutoShorts.dmg
	./macos/build.sh

demo:             ## make a rights-free demo episode and turn it into clips
	$(PY) backend/scripts/make_demo_episode.py ~/Movies/autoshorts_demo.mp4
	$(CF) add-file ~/Movies/autoshorts_demo.mp4 --credit "Demo Podcast"

test:
	cd backend && .venv/bin/pytest -q

clean:
	rm -rf dist frontend/dist
