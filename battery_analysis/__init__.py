"""
battery_analysis
=================
Config- and database-driven successor to the flat `battery_analysis`
scripts. See the top-level Project class for the main entry point:

    from battery_analysis.project import Project
    project = Project("path/to/LIBs")
    project.import_eis()
    project.fit_eis()
    project.analyze_eis()
    project.plot_eis()
"""

__version__ = "0.1.0"
