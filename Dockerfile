from python:3-alpine

run apk add --no-cache --virtual .build-deps gcc g++ make libffi-dev openssl-dev

copy ["README.md", "setup.py", "/modbus4mqtt/"]
copy ["./modbus4mqtt/*", "/modbus4mqtt/modbus4mqtt/"]

run pip install /modbus4mqtt

run apk del .build-deps

entrypoint ["modbus4mqtt"]
