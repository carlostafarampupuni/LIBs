"""
XPS: raw spectra are fitted, already-fitted exports are plotted as-is (decided per file),
organised in batches like every other technique. Configured by xps.yaml.

    project.use("xps", "xps.yaml")
    project.fit_xps(batch="Batch1")      # tables -> Exports/XPS_Batch1.xlsx
    project.plot_xps(batch="Batch1")     # figures -> Figures/XPS/Batch1/, their data -> Exports/PlotData/XPS/Batch1/

Modules: fitting (physics), sources (what to fit / plot), batches, pubplot (figures),
plotdata (figure data for Origin/SigmaPlot), tuner (interactive), template (install into a project).
"""
