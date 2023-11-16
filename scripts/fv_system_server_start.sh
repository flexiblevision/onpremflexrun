export PYTHONPATH="${PYTHONPATH}:${HOME}/flex-run"

forever start -c python3 $HOME/flex-run/system_server/server.py
