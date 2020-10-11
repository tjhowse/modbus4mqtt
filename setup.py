import setuptools

with open("README.md", "r") as fh:
    long_description = fh.read()

setuptools.setup(
    name="modbus4mqtt",
    version="0.3.1",
    author="Travis Howse",
    author_email="tjhowse@gmail.com",
    description="A YAML-defined bidirectional Modbus to MQTT interface",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/tjhowse/modbus4mqtt",
    packages=setuptools.find_packages(),
    install_requires=[
        'pyyaml>=3.5.0',
        'paho-mqtt>=1.5.0',
        'pymodbus>=2.3.0',
        'click>=6.7',
        'SungrowModbusTcpClient>=0.1.2',
    ],
    tests_require=[
        'nose2>=0.9.2',
        'nose2[coverage_plugin]>=0.6.5',
    ],
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: GNU General Public License v3 (GPLv3)",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.5.0',
    test_suite='nose2.collector.collector',
    entry_points='''
        [console_scripts]
        modbus4mqtt=modbus4mqtt.modbus4mqtt:main
    ''',
)
