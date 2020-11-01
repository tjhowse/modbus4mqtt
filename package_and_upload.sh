#!/bin/bash
rm ./dist/modbus4mqtt*.whl
rm ./dist/modbus4mqtt*.tar.gz
python3 setup.py sdist bdist_wheel
python3 -m twine upload dist/*
