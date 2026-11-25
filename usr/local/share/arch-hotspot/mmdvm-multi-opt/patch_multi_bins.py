#!/usr/bin/env python3
from pathlib import Path
import sys
root = Path(sys.argv[1] if len(sys.argv) > 1 else '.')

# --- MMDVM-Multi.cpp ---
p = root / 'MMDVM-Multi.cpp'
s = p.read_text(encoding='utf-8')
if '#include <fstream>' not in s:
    s = s.replace('#include <cmath>\n', '#include <cmath>\n#include <fstream>\n#include <sstream>\n#include <vector>\n#include <algorithm>\n', 1)
helper = r'''
static std::vector<unsigned int> archReadBins(const char* key)
{
    std::vector<unsigned int> bins;
    std::ifstream f("/etc/mmdvm/channel-bins");
    if (!f.is_open())
        return bins;
    std::string line;
    while (std::getline(f, line)) {
        std::string::size_type eq = line.find('=');
        if (eq == std::string::npos)
            continue;
        if (line.substr(0U, eq) != key)
            continue;
        std::stringstream ss(line.substr(eq + 1U));
        std::string item;
        while (std::getline(ss, item, ',')) {
            try {
                bins.push_back((unsigned int)std::stoul(item));
            } catch (...) {
            }
        }
        break;
    }
    return bins;
}

static std::vector<unsigned int> archDefaultBins(unsigned int active, unsigned int pfb)
{
    std::vector<unsigned int> bins(active, 0U);
    unsigned int maxReal = pfb / 2U - 1U;
    maxReal = std::min<unsigned int>(maxReal, 4U);
    unsigned int fillReal = std::min<unsigned int>(maxReal, active);
    for (unsigned int i = 0U; i < fillReal; i++)
        bins[i] = i;
    for (unsigned int m = pfb - 1U, p = fillReal; p < active; m--, p++)
        bins[p] = m;
    return bins;
}

static bool archValidateBins(const std::vector<unsigned int>& bins, unsigned int active, unsigned int pfb)
{
    if (bins.size() != active)
        return false;
    for (unsigned int i = 0U; i < active; i++) {
        if (bins[i] >= pfb)
            return false;
        for (unsigned int j = i + 1U; j < active; j++) {
            if (bins[i] == bins[j])
                return false;
        }
    }
    return true;
}
'''
if 'archReadBins' not in s:
    s = s.replace('int main(int argc, char** argv)', helper + '\nint main(int argc, char** argv)', 1)
anchor = '''    if (active_channels >= num_pfb_channels) {
        ::LogError("The number of channels must be lower than %u for this sample rate", num_pfb_channels);
        return 1;
    }
'''
insert = anchor + '''
    std::vector<unsigned int> rx_bins = archReadBins("RX");
    std::vector<unsigned int> tx_bins = archReadBins("TX");
    if (!archValidateBins(rx_bins, active_channels, num_pfb_channels)) {
        rx_bins = archDefaultBins(active_channels, num_pfb_channels);
        ::LogWarning("Invalid RX channel bin map, using default PFB mapping");
    }
    if (!archValidateBins(tx_bins, active_channels, num_pfb_channels)) {
        tx_bins = archDefaultBins(active_channels, num_pfb_channels);
        ::LogWarning("Invalid TX channel bin map, using default PFB mapping");
    }
'''
if 'archReadBins("RX")' not in s:
    if anchor not in s:
        raise SystemExit('anchor for bin validation not found')
    s = s.replace(anchor, insert, 1)
s = s.replace('''    Receiver* rx = new Receiver(network, device, fm_mod, resampler, rotator, channelizer,
                                timing, active_channels, num_pfb_channels, power_calibration, symbol_deviation, needs_timestamp);''',
'''    Receiver* rx = new Receiver(network, device, fm_mod, resampler, rotator, channelizer,
                                timing, active_channels, num_pfb_channels, rx_bins, power_calibration, symbol_deviation, needs_timestamp);''', 1)
s = s.replace('''    Transmitter* tx = new Transmitter(network, device, fm_mod, resampler, rotator, channelizer,
                                      timing, active_channels, num_pfb_channels, needs_timestamp, dac_scaling, symbol_deviation);''',
'''    Transmitter* tx = new Transmitter(network, device, fm_mod, resampler, rotator, channelizer,
                                      timing, active_channels, num_pfb_channels, tx_bins, needs_timestamp, dac_scaling, symbol_deviation);''', 1)
p.write_text(s, encoding='utf-8')

# --- Receiver.h ---
p = root / 'Receiver.h'
s = p.read_text(encoding='utf-8')
s = s.replace('''             Channelizer* channelizer, DMRTiming* burst_timer, unsigned int num_active_channels,
             unsigned int num_pfb_channels, float power_calibration, float symbol_deviation, bool needs_timestamp);''',
'''             Channelizer* channelizer, DMRTiming* burst_timer, unsigned int num_active_channels,
             unsigned int num_pfb_channels, const std::vector<unsigned int>& channel_bins,
             float power_calibration, float symbol_deviation, bool needs_timestamp);''', 1)
if '#include <vector>' not in s:
    s = s.replace('#include <cstdint>\n', '#include <cstdint>\n#include <vector>\n', 1)
if 'std::vector<unsigned int> m_channelBins;' not in s:
    s = s.replace('    unsigned int m_fillReal;\n', '    unsigned int m_fillReal;\n    std::vector<unsigned int> m_channelBins;\n', 1)
p.write_text(s, encoding='utf-8')

# --- Receiver.cpp ---
p = root / 'Receiver.cpp'
s = p.read_text(encoding='utf-8')
s = s.replace('''                   Channelizer* channelizer, DMRTiming* burst_timer, unsigned int num_active_channels,
                   unsigned int num_pfb_channels, float power_calibration, float symbol_deviation, bool needs_timestamp) : ''',
'''                   Channelizer* channelizer, DMRTiming* burst_timer, unsigned int num_active_channels,
                   unsigned int num_pfb_channels, const std::vector<unsigned int>& channel_bins,
                   float power_calibration, float symbol_deviation, bool needs_timestamp) : ''', 1)
s = s.replace('m_pfbChannels(num_pfb_channels),', 'm_pfbChannels(num_pfb_channels),\n    m_channelBins(channel_bins),', 1)
old = '''        // First four usable channels are on the real side of the FFT, the rest in imag,
        // reversed order to minimize occupied BW for 250k sps
        // Channel 5 (6?) of the PFB wraps around to the imag side and is not usable
        for (unsigned int k = 0U; k < m_fillReal; k++) {
            for (unsigned int j = 0U; j < RX_INTERP_IN_SIZE; j++)
                rearranged[k][j] = channelized2[k][j];
        }

        for (unsigned int m = m_pfbChannels - 1U, p = m_fillReal; p < m_activeChannels; m--, p++) {
            for (unsigned int j = 0U; j < RX_INTERP_IN_SIZE; j++)
                rearranged[p][j] = channelized2[m][j];
        }
'''
new = '''        for (unsigned int p = 0U; p < m_activeChannels; p++) {
            unsigned int bin = m_channelBins[p];
            if (bin >= m_pfbChannels)
                bin = p % m_pfbChannels;
            for (unsigned int j = 0U; j < RX_INTERP_IN_SIZE; j++)
                rearranged[p][j] = channelized2[bin][j];
        }
'''
if old not in s:
    raise SystemExit('Receiver reorder block not found')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')

# --- Transmitter.h ---
p = root / 'Transmitter.h'
s = p.read_text(encoding='utf-8')
s = s.replace('''                Channelizer* channelizer, DMRTiming* burst_timer,
                unsigned int num_active_channels, unsigned int num_pfb_channels, bool needs_timestamp,
                float dac_scaling, float symbol_deviation);''',
'''                Channelizer* channelizer, DMRTiming* burst_timer,
                unsigned int num_active_channels, unsigned int num_pfb_channels,
                const std::vector<unsigned int>& channel_bins, bool needs_timestamp,
                float dac_scaling, float symbol_deviation);''', 1)
if 'std::vector<unsigned int> m_channelBins;' not in s:
    s = s.replace('    unsigned int m_fillReal;\n', '    unsigned int m_fillReal;\n    std::vector<unsigned int> m_channelBins;\n', 1)
p.write_text(s, encoding='utf-8')

# --- Transmitter.cpp ---
p = root / 'Transmitter.cpp'
s = p.read_text(encoding='utf-8')
s = s.replace('''                         Channelizer* channelizer, DMRTiming* burst_timer,
                         unsigned int num_active_channels, unsigned int num_pfb_channels, bool needs_timestamp,
                         float dac_scaling, float symbol_deviation) : ''',
'''                         Channelizer* channelizer, DMRTiming* burst_timer,
                         unsigned int num_active_channels, unsigned int num_pfb_channels,
                         const std::vector<unsigned int>& channel_bins, bool needs_timestamp,
                         float dac_scaling, float symbol_deviation) : ''', 1)
s = s.replace('m_pfbChannels(num_pfb_channels),', 'm_pfbChannels(num_pfb_channels),\n    m_channelBins(channel_bins),', 1)
old = '''    std::complex<float> channelized[TX_SAMP_OUT_SIZE] = {0.0F, 0.0F};
    std::complex<float> channels[MAX_PFB_CHANNELS] = {0.0F, 0.0F};

    for (unsigned int i = 0U; i < TX_INTERP_OUT_SIZE; i++) {
        for (unsigned int k = 0U; k < m_fillReal; k++)
            channels[k] = channelizer_samples[k][i] * m_DACScaling / float(m_activeChannels);

        for (unsigned int m = m_pfbChannels - 1U, p = m_fillReal; p < m_activeChannels; m--, p++)
            channels[m] = channelizer_samples[p][i] * m_DACScaling / float(m_activeChannels);

        m_channelizer->synthesize(channels, &channelized[i * m_pfbChannels]);
    }
'''
new = '''    std::complex<float> channelized[TX_SAMP_OUT_SIZE] = {0.0F, 0.0F};
    std::complex<float> channels[MAX_PFB_CHANNELS] = {0.0F, 0.0F};

    for (unsigned int i = 0U; i < TX_INTERP_OUT_SIZE; i++) {
        std::fill(channels, channels + m_pfbChannels, std::complex<float>(0.0F, 0.0F));
        for (unsigned int p = 0U; p < m_activeChannels; p++) {
            unsigned int bin = m_channelBins[p];
            if (bin >= m_pfbChannels)
                bin = p % m_pfbChannels;
            channels[bin] = channelizer_samples[p][i] * m_DACScaling / float(m_activeChannels);
        }

        m_channelizer->synthesize(channels, &channelized[i * m_pfbChannels]);
    }
'''
if old not in s:
    raise SystemExit('Transmitter channel assembly block not found')
s = s.replace(old, new, 1)
p.write_text(s, encoding='utf-8')

print('patched', root)
