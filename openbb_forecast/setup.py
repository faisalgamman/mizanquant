from setuptools import setup, find_packages

setup(
    name="openbb-forecast",
    version="0.1.0",
    packages=find_packages(),
    entry_points={
        "openbb_core_extension": [
            "forecast = openbb_forecast.router:router",
        ],
    },
)
