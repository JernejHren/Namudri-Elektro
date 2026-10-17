# Namudri Elektro

Home Assistant custom integration for Slovenian **omrežnina**: 15-minute power averages, end-of-window projection, configurable dogovorjena moč, and built-in **load shedding** in tariff blocks 1–2.

## Dependencies

Install separately (e.g. via HACS):

- [Elektro Network Tariff](https://github.com/frlequ/home-assistant-network-tariff) — current block 1–5  
- A **power sensor in W** (e.g. Shelly 3EM total power)

## How projection works

Billing windows are fixed at **:00, :15, :30, :45** (900 s).

1. Integrate import power over the current window (export ≤ 0 → 0 W).  
2. **Running average** = energy so far / elapsed time.  
3. **Projected average** = (energy so far + current power × remaining) / 900 s.  
4. **Headroom** = agreed power for current block − projected average.

## Load shedding (blocks 1–2)

When projection &gt; agreed + threshold (after stabilization delay):

1. Turn off the next **on** device in priority order.  
2. Remember previous state (climate HVAC mode / setpoint).  
3. Only restore devices **this integration** turned off.  
4. Manual offs stay off; if you turn a shed device back on, it leaves the auto-shed list.  
5. Restore when projection &lt; agreed − hysteresis and min-off time elapsed (one device per cycle).  
6. Outside blocks 1–2, auto-shed devices are restored.

Master switch **Load shedding** disables the controller and restores everything.

## Installation

1. Copy `custom_components/namudri_elektro` into HA (or HACS custom repo).  
2. Restart Home Assistant.  
3. Add **Namudri Elektro** — pick power + tariff sensors, then optional devices.  
4. Set T1–T5 agreed power (kW) and shed tuning numbers.  
5. Enable **Load shedding** switch.

Existing installs: **Configure** the integration → add devices on the shedding step → reload/restart if needed.

## Entities

| Entity | Description |
|--------|-------------|
| `sensor.*_projected_15_min_power` | Projected end-of-window average (W) |
| `sensor.*_running_15_min_average` | Running average (W) |
| `sensor.*_completed_15_min_average` | Last completed window (W) |
| `sensor.*_agreed_power_*` | Agreed limit for current block (W) |
| `sensor.*_headroom` | Margin vs projection (W) |
| `sensor.*_tariff_block` | Block 1–5 |
| `sensor.*_shed_device_count` | How many devices are auto-shed |
| `binary_sensor.*_over_agreed_power` | Projection above agreed |
| `binary_sensor.*_shedding_active` | Auto-shed list non-empty |
| `switch.*_load_shedding` | Master enable |
| `number.*_agreed_power_t1` … `t5` | Dogovorjena moč (**kW**) |
| `number.*_shed_threshold` | W above limit before shed |
| `number.*_restore_hysteresis` | W below limit before restore |
| `number.*_minimum_off_time` | Minutes |
| `number.*_projection_stabilization` | Seconds into window before shedding |

## Service

- `namudri_elektro.restore_all` — restore all auto-shed devices (optional `entry_id`)

## License

Apache-2.0
