from . import air

# QIDI's load-cell probe.  air.so's PrinterAirProbe predates the probe
# interface's optional gcmd arguments: get_offsets() takes none and
# get_probe_params() needs one.  Callers since 2025 pass get_offsets(gcmd)
# and may call get_probe_params() alone.
class PrinterAirProbe(air.PrinterAirProbe):
    def get_offsets(self, gcmd=None):
        return air.PrinterAirProbe.get_offsets(self)
    def get_probe_params(self, gcmd=None):
        return air.PrinterAirProbe.get_probe_params(self, gcmd)

def load_config(config):
    return PrinterAirProbe(config)
