from python:3-alpine

run apk add --no-cache --virtual .build-deps gcc g++ make libffi-dev openssl-dev git

copy ["./dist/*.whl", "/modbus4mqtt/"]

run pip install /modbus4mqtt/*.whl

run apk del .build-deps

entrypoint ["modbus4mqtt"]
