#!/bin/bash
version=v`cat ./modbus4mqtt/version.py | cut -d\" -f2`
rm ./dist/modbus4mqtt*.whl
rm ./dist/modbus4mqtt*.tar.gz
python3 setup.py sdist bdist_wheel
python3 -m twine upload dist/*
docker build -t tjhowse/modbus4mqtt:latest -t tjhowse/modbus4mqtt:"$version" .
docker push tjhowse/modbus4mqtt:latest
docker push tjhowse/modbus4mqtt:"$version"
