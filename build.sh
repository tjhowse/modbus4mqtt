#!/bin/bash

rm -r dist
uv build .
docker build -t tjhowse/modbus4mqtt:1.0.0-rc2 .
