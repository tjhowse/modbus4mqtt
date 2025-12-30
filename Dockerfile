from python:3-alpine

run apk add --no-cache --virtual .build-deps gcc g++ make libffi-dev openssl-dev git

copy ["./dist/*.whl", "/modbus4mqtt/"]

copy ["./modbus4mqtt/config", "/modbus4mqtt/config/"]

run pip install /modbus4mqtt/*.whl

run apk del .build-deps

run rm /modbus4mqtt/*.whl

entrypoint ["modbus4mqtt"]
