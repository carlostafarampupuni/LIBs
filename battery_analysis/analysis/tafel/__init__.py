"""
Tafel / Butler–Volmer kinetics from galvanostatic pulses (tafel.yaml, tafel.ipynb).

    project.use("tafel", "tafel.yaml")
    project.fit_tafel(batch="Batch1")     # tables -> Exports/Tafel_Batch1.xlsx
    project.plot_tafel(batch="Batch1")    # figures -> Figures/Tafel/Batch1/

See analysis.py for the four methods (excel, butler_volmer, tafel_region, linear).
"""
