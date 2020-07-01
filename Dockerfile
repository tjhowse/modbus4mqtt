from python:3

copy ["README.md", "setup.py", "/modbus4mqtt/"]
copy ["./modbus4mqtt/*", "/modbus4mqtt/modbus4mqtt/"]

run pip install /modbus4mqtt

entrypoint ["modbus4mqtt"]
