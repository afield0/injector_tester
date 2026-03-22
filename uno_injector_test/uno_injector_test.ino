/*
  Uno R3 injector pulse controller - mask-based ISR version
  ---------------------------------------------------------
  IMPORTANT:
  - Do NOT connect fuel injectors directly to Arduino pins.
  - Use proper low-side drivers / MOSFETs and flyback/clamp protection.
  - Use an external supply for injectors.
  - Share ground between Arduino and injector power supply.

  Outputs:
    CH1 -> D5
    CH2 -> D6
    CH3 -> D7
    CH4 -> D8

  Serial commands:
    HELP
    STATUS
    MODEL <0|1>
    SET <channel> <rpm> <dutyPercent>
    SETMASK <mask 1-15> <rpm> <dutyPercent>
    START <channel>
    RUN <channel> <pulses>
    STOP <channel>
    STARTMASK <mask 1-15>
    RUNMASK <mask 1-15> <pulses>
    STOPMASK <mask 1-15>
    STARTALL
    STOPALL

  Grouped mask command semantics:
    STARTMASK and RUNMASK initialize all selected outputs from the inactive phase
    and apply timing state together as part of one command handling path.

  Pulse models:
    0 = 4-stroke, 1 event every 2 revs  -> Hz = RPM / 120
    1 = 1 event every 1 rev             -> Hz = RPM / 60
*/

#include <Arduino.h>
#include <avr/io.h>
#include <avr/interrupt.h>

// -----------------------------
// Configuration
// -----------------------------
static const uint8_t NUM_CHANNELS = 4;

// Logical channel bits used by the control masks.
static const uint8_t CH1_BIT = _BV(0);
static const uint8_t CH2_BIT = _BV(1);
static const uint8_t CH3_BIT = _BV(2);
static const uint8_t CH4_BIT = _BV(3);
static const uint8_t OUTPUT_MASK = CH1_BIT | CH2_BIT | CH3_BIT | CH4_BIT;

// Physical output mapping: CH1..CH3 on D5..D7 (PORTD), CH4 on D8 (PORTB).
static const uint8_t CH1_PORTD_BIT = _BV(PD5);
static const uint8_t CH2_PORTD_BIT = _BV(PD6);
static const uint8_t CH3_PORTD_BIT = _BV(PD7);
static const uint8_t CH4_PORTB_BIT = _BV(PB0);
static const uint8_t OUTPUT_MASK_PORTD = CH1_PORTD_BIT | CH2_PORTD_BIT | CH3_PORTD_BIT;
static const uint8_t OUTPUT_MASK_PORTB = CH4_PORTB_BIT;

static const uint8_t channelBits[NUM_CHANNELS] = {
  CH1_BIT, CH2_BIT, CH3_BIT, CH4_BIT
};

// Timer tick in microseconds.
// 20 us is a nice compromise on Uno. Change to 10 if you want finer granularity.
static const uint16_t TICK_US = 20;

// -----------------------------
// Pulse model
// -----------------------------
enum PulseModel : uint8_t {
  FOUR_STROKE_ONE_EVENT_PER_CYCLE = 0, // Hz = RPM / 120
  ONE_EVENT_PER_REV = 1                // Hz = RPM / 60
};

// -----------------------------
// Shared ISR state
// -----------------------------
volatile uint8_t activeMask = 0;   // channels enabled for pulsing
volatile uint8_t stateMask  = 0;   // channels currently in the active pulse phase
volatile uint8_t finiteRunMask = 0;    // channels running for a fixed pulse count
volatile uint8_t stopAfterLowMask = 0; // channels that should stop after current HIGH pulse ends

volatile uint16_t onTicks[NUM_CHANNELS];
volatile uint16_t offTicks[NUM_CHANNELS];
volatile uint16_t ticksLeft[NUM_CHANNELS];
volatile uint32_t pulsesRemaining[NUM_CHANNELS];

// model read by serial side and timing calculation
volatile uint8_t pulseModel = FOUR_STROKE_ONE_EVENT_PER_CYCLE;

// Shadow config values for reporting / recalculation
float rpmCfg[NUM_CHANNELS];
float dutyCfg[NUM_CHANNELS];

String inputLine;

// -----------------------------
// Utility
// -----------------------------
float rpmToHz(float rpm, uint8_t model) {
  if (model == FOUR_STROKE_ONE_EVENT_PER_CYCLE) {
    return rpm / 120.0f;
  }
  return rpm / 60.0f;
}

String getToken(String &line, int index) {
  int found = 0;
  int start = 0;
  int len = line.length();

  for (int i = 0; i <= len; i++) {
    if (i == len || line.charAt(i) == ' ') {
      if (found == index) {
        return line.substring(start, i);
      }
      found++;
      start = i + 1;
    }
  }
  return "";
}

bool parseUInt8(const String& s, uint8_t& out) {
  if (s.length() == 0) return false;
  long v = s.toInt();
  if (v < 0 || v > 255) return false;
  out = (uint8_t)v;
  return true;
}

bool parseUInt32(const String& s, uint32_t& out) {
  if (s.length() == 0) return false;

  uint32_t value = 0;
  for (int i = 0; i < s.length(); i++) {
    char c = s.charAt(i);
    if (c < '0' || c > '9') return false;

    uint32_t digit = (uint32_t)(c - '0');
    if (value > 429496729UL || (value == 429496729UL && digit > 5UL)) {
      return false;
    }
    value = value * 10UL + digit;
  }

  out = value;
  return true;
}

bool parseChannelMask(const String& s, uint8_t& out) {
  uint8_t value;
  if (!parseUInt8(s, value)) return false;
  if (value == 0 || value > OUTPUT_MASK) return false;
  out = value;
  return true;
}

// -----------------------------
// Channel control helpers
// -----------------------------
void forceOutputsOff(uint8_t mask) {
  uint8_t setMaskD = 0;
  uint8_t setMaskB = 0;

  if (mask & CH1_BIT) setMaskD |= CH1_PORTD_BIT;
  if (mask & CH2_BIT) setMaskD |= CH2_PORTD_BIT;
  if (mask & CH3_BIT) setMaskD |= CH3_PORTD_BIT;
  if (mask & CH4_BIT) setMaskB |= CH4_PORTB_BIT;

  PORTD |= setMaskD;
  PORTB |= setMaskB;
}

void startMaskChannels(uint8_t mask) {
  mask &= OUTPUT_MASK;
  if (mask == 0) return;

  noInterrupts();
  activeMask |= mask;
  stateMask &= ~mask;          // begin in the inactive phase
  finiteRunMask &= ~mask;
  stopAfterLowMask &= ~mask;

  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    if (mask & channelBits[i]) {
      pulsesRemaining[i] = 0;
      ticksLeft[i] = offTicks[i]; // begin with OFF delay, then first ON edge
    }
  }
  interrupts();

  forceOutputsOff(mask);
}

void runMaskChannelsForPulses(uint8_t mask, uint32_t pulses) {
  mask &= OUTPUT_MASK;
  if (mask == 0 || pulses == 0) return;

  noInterrupts();
  activeMask |= mask;
  stateMask &= ~mask;
  finiteRunMask |= mask;
  stopAfterLowMask &= ~mask;

  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    if (mask & channelBits[i]) {
      pulsesRemaining[i] = pulses;
      ticksLeft[i] = offTicks[i];
    }
  }
  interrupts();

  forceOutputsOff(mask);
}

void stopMaskChannels(uint8_t mask) {
  mask &= OUTPUT_MASK;
  if (mask == 0) return;

  noInterrupts();
  activeMask &= ~mask;
  stateMask &= ~mask;
  finiteRunMask &= ~mask;
  stopAfterLowMask &= ~mask;

  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    if (mask & channelBits[i]) {
      pulsesRemaining[i] = 0;
    }
  }
  interrupts();

  forceOutputsOff(mask);
}

void startChannel(uint8_t idx) {
  if (idx >= NUM_CHANNELS) return;
  startMaskChannels(channelBits[idx]);
}

void runChannelForPulses(uint8_t idx, uint32_t pulses) {
  if (idx >= NUM_CHANNELS || pulses == 0) return;
  runMaskChannelsForPulses(channelBits[idx], pulses);
}

void stopChannel(uint8_t idx) {
  if (idx >= NUM_CHANNELS) return;
  stopMaskChannels(channelBits[idx]);
}

void setChannelConfig(uint8_t idx, float rpm, float duty) {
  if (idx >= NUM_CHANNELS) return;
  rpmCfg[idx] = rpm;
  dutyCfg[idx] = duty;
  recalcChannel(idx);
}

void setMaskConfig(uint8_t mask, float rpm, float duty) {
  mask &= OUTPUT_MASK;
  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    if (mask & channelBits[i]) {
      setChannelConfig(i, rpm, duty);
    }
  }
}

void startAllChannels() {
  startMaskChannels(OUTPUT_MASK);
}

void stopAllChannels() {
  stopMaskChannels(OUTPUT_MASK);
}

// -----------------------------
// Timing calculation
// -----------------------------
void recalcChannel(uint8_t idx) {
  if (idx >= NUM_CHANNELS) return;

  float rpm = rpmCfg[idx];
  float duty = dutyCfg[idx];

  if (rpm < 1.0f) rpm = 1.0f;
  if (rpm > 50000.0f) rpm = 50000.0f;

  if (duty < 0.1f) duty = 0.1f;
  if (duty > 95.0f) duty = 95.0f;

  rpmCfg[idx] = rpm;
  dutyCfg[idx] = duty;

  uint8_t model;
  noInterrupts();
  model = pulseModel;
  interrupts();

  float hz = rpmToHz(rpm, model);
  if (hz < 0.01f) hz = 0.01f;

  float periodUs = 1000000.0f / hz;
  float onUs = periodUs * (duty / 100.0f);
  float offUs = periodUs - onUs;

  uint32_t onT = (uint32_t)(onUs / TICK_US + 0.5f);
  uint32_t offT = (uint32_t)(offUs / TICK_US + 0.5f);

  if (onT < 1) onT = 1;
  if (offT < 1) offT = 1;
  if (onT > 65535UL) onT = 65535UL;
  if (offT > 65535UL) offT = 65535UL;

  noInterrupts();
  onTicks[idx] = (uint16_t)onT;
  offTicks[idx] = (uint16_t)offT;

  // If channel is inactive, preload it for a clean later START.
  if ((activeMask & channelBits[idx]) == 0) {
    ticksLeft[idx] = offTicks[idx];
  }
  interrupts();
}

// -----------------------------
// Timer ISR
// -----------------------------
ISR(TIMER1_COMPA_vect) {
  uint8_t active = activeMask;
  uint8_t setMask = 0;
  uint8_t clearMask = 0;

  if (active & CH1_BIT) {
    uint16_t t = ticksLeft[0];
    if (t > 1) {
      ticksLeft[0] = t - 1;
    } else {
      if (stateMask & CH1_BIT) {
        stateMask &= ~CH1_BIT;
        clearMask |= CH1_BIT;
        if (stopAfterLowMask & CH1_BIT) {
          activeMask &= ~CH1_BIT;
          finiteRunMask &= ~CH1_BIT;
          stopAfterLowMask &= ~CH1_BIT;
          pulsesRemaining[0] = 0;
        } else {
          ticksLeft[0] = offTicks[0];
        }
      } else {
        stateMask |= CH1_BIT;
        setMask |= CH1_BIT;
        ticksLeft[0] = onTicks[0];
        if (finiteRunMask & CH1_BIT) {
          uint32_t remaining = pulsesRemaining[0];
          if (remaining > 0) {
            remaining--;
            pulsesRemaining[0] = remaining;
            if (remaining == 0) {
              stopAfterLowMask |= CH1_BIT;
            }
          }
        }
      }
    }
  }

  if (active & CH2_BIT) {
    uint16_t t = ticksLeft[1];
    if (t > 1) {
      ticksLeft[1] = t - 1;
    } else {
      if (stateMask & CH2_BIT) {
        stateMask &= ~CH2_BIT;
        clearMask |= CH2_BIT;
        if (stopAfterLowMask & CH2_BIT) {
          activeMask &= ~CH2_BIT;
          finiteRunMask &= ~CH2_BIT;
          stopAfterLowMask &= ~CH2_BIT;
          pulsesRemaining[1] = 0;
        } else {
          ticksLeft[1] = offTicks[1];
        }
      } else {
        stateMask |= CH2_BIT;
        setMask |= CH2_BIT;
        ticksLeft[1] = onTicks[1];
        if (finiteRunMask & CH2_BIT) {
          uint32_t remaining = pulsesRemaining[1];
          if (remaining > 0) {
            remaining--;
            pulsesRemaining[1] = remaining;
            if (remaining == 0) {
              stopAfterLowMask |= CH2_BIT;
            }
          }
        }
      }
    }
  }

  if (active & CH3_BIT) {
    uint16_t t = ticksLeft[2];
    if (t > 1) {
      ticksLeft[2] = t - 1;
    } else {
      if (stateMask & CH3_BIT) {
        stateMask &= ~CH3_BIT;
        clearMask |= CH3_BIT;
        if (stopAfterLowMask & CH3_BIT) {
          activeMask &= ~CH3_BIT;
          finiteRunMask &= ~CH3_BIT;
          stopAfterLowMask &= ~CH3_BIT;
          pulsesRemaining[2] = 0;
        } else {
          ticksLeft[2] = offTicks[2];
        }
      } else {
        stateMask |= CH3_BIT;
        setMask |= CH3_BIT;
        ticksLeft[2] = onTicks[2];
        if (finiteRunMask & CH3_BIT) {
          uint32_t remaining = pulsesRemaining[2];
          if (remaining > 0) {
            remaining--;
            pulsesRemaining[2] = remaining;
            if (remaining == 0) {
              stopAfterLowMask |= CH3_BIT;
            }
          }
        }
      }
    }
  }

  if (active & CH4_BIT) {
    uint16_t t = ticksLeft[3];
    if (t > 1) {
      ticksLeft[3] = t - 1;
    } else {
      if (stateMask & CH4_BIT) {
        stateMask &= ~CH4_BIT;
        clearMask |= CH4_BIT;
        if (stopAfterLowMask & CH4_BIT) {
          activeMask &= ~CH4_BIT;
          finiteRunMask &= ~CH4_BIT;
          stopAfterLowMask &= ~CH4_BIT;
          pulsesRemaining[3] = 0;
        } else {
          ticksLeft[3] = offTicks[3];
        }
      } else {
        stateMask |= CH4_BIT;
        setMask |= CH4_BIT;
        ticksLeft[3] = onTicks[3];
        if (finiteRunMask & CH4_BIT) {
          uint32_t remaining = pulsesRemaining[3];
          if (remaining > 0) {
            remaining--;
            pulsesRemaining[3] = remaining;
            if (remaining == 0) {
              stopAfterLowMask |= CH4_BIT;
            }
          }
        }
      }
    }
  }

  // Active-low outputs: setMask drives the pin LOW (injector ON), clearMask drives it HIGH (injector OFF).
  uint8_t setMaskD = 0;
  uint8_t clearMaskD = 0;
  uint8_t setMaskB = 0;
  uint8_t clearMaskB = 0;

  if (setMask & CH1_BIT) setMaskD |= CH1_PORTD_BIT;
  if (setMask & CH2_BIT) setMaskD |= CH2_PORTD_BIT;
  if (setMask & CH3_BIT) setMaskD |= CH3_PORTD_BIT;
  if (setMask & CH4_BIT) setMaskB |= CH4_PORTB_BIT;

  if (clearMask & CH1_BIT) clearMaskD |= CH1_PORTD_BIT;
  if (clearMask & CH2_BIT) clearMaskD |= CH2_PORTD_BIT;
  if (clearMask & CH3_BIT) clearMaskD |= CH3_PORTD_BIT;
  if (clearMask & CH4_BIT) clearMaskB |= CH4_PORTB_BIT;

  uint8_t pd = PORTD;
  pd &= ~setMaskD;
  pd |= clearMaskD;
  PORTD = pd;

  uint8_t pb = PORTB;
  pb &= ~setMaskB;
  pb |= clearMaskB;
  PORTB = pb;
}

// -----------------------------
// Timer setup
// -----------------------------
void setupTimer1() {
  noInterrupts();

  TCCR1A = 0;
  TCCR1B = 0;
  TCNT1 = 0;

  // CTC mode
  TCCR1B |= _BV(WGM12);

  // Prescaler = 8
  // 16 MHz / 8 = 2 MHz timer clock
  // 1 timer count = 0.5 us
  TCCR1B |= _BV(CS11);

  // OCR1A = (TICK_US / 0.5us) - 1 = (TICK_US * 2) - 1
  OCR1A = (uint16_t)(TICK_US * 2 - 1);

  // Enable compare match interrupt
  TIMSK1 |= _BV(OCIE1A);

  interrupts();
}

// -----------------------------
// Reporting
// -----------------------------
void printHelp() {
  Serial.println(F("Commands:"));
  Serial.println(F("  HELP"));
  Serial.println(F("  STATUS"));
  Serial.println(F("  MODEL <0|1>"));
  Serial.println(F("  SET <channel 1-4> <rpm> <dutyPercent>"));
  Serial.println(F("  SETMASK <mask 1-15> <rpm> <dutyPercent>"));
  Serial.println(F("  START <channel 1-4>"));
  Serial.println(F("  STARTMASK <mask 1-15>"));
  Serial.println(F("  RUN <channel 1-4> <pulses>"));
  Serial.println(F("  RUNMASK <mask 1-15> <pulses>"));
  Serial.println(F("  STOP <channel 1-4>"));
  Serial.println(F("  STOPMASK <mask 1-15>"));
  Serial.println(F("  STARTALL"));
  Serial.println(F("  STOPALL"));
  Serial.println(F(""));
  Serial.println(F("Grouped mask semantics:"));
  Serial.println(F("  STARTMASK and RUNMASK initialize selected outputs from the"));
  Serial.println(F("  inactive phase and apply timing state together in one path."));
  Serial.println(F(""));
  Serial.println(F("Models:"));
  Serial.println(F("  0 = 4-stroke, 1 event per 2 revs (Hz = RPM/120)"));
  Serial.println(F("  1 = 1 event per rev            (Hz = RPM/60)"));
}

void printStatus() {
  uint8_t active, state, model, finite, stopAfterLow;
  uint16_t onT[NUM_CHANNELS], offT[NUM_CHANNELS], leftT[NUM_CHANNELS];
  uint32_t pulsesLeft[NUM_CHANNELS];

  noInterrupts();
  active = activeMask;
  state = stateMask;
  model = pulseModel;
  finite = finiteRunMask;
  stopAfterLow = stopAfterLowMask;
  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    onT[i] = onTicks[i];
    offT[i] = offTicks[i];
    leftT[i] = ticksLeft[i];
    pulsesLeft[i] = pulsesRemaining[i];
  }
  interrupts();

  Serial.print(F("MODEL "));
  Serial.println(model);
  Serial.print(F("TICK_US "));
  Serial.println(TICK_US);
  Serial.print(F("ACTIVE_MASK 0x"));
  Serial.println(active, HEX);
  Serial.print(F("STATE_MASK 0x"));
  Serial.println(state, HEX);

  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    uint8_t bit = channelBits[i];
    Serial.print(F("CH "));
    Serial.print(i + 1);
    Serial.print(F(" enabled="));
    Serial.print((active & bit) ? 1 : 0);
    Serial.print(F(" state="));
    Serial.print((state & bit) ? 1 : 0);
    Serial.print(F(" mode="));
    Serial.print((finite & bit) ? F("COUNTED") : F("CONT"));
    Serial.print(F(" rpm="));
    Serial.print(rpmCfg[i], 1);
    Serial.print(F(" duty="));
    Serial.print(dutyCfg[i], 1);
    Serial.print(F(" onTicks="));
    Serial.print(onT[i]);
    Serial.print(F(" offTicks="));
    Serial.print(offT[i]);
    Serial.print(F(" ticksLeft="));
    Serial.print(leftT[i]);
    Serial.print(F(" pulsesLeft="));
    Serial.print(pulsesLeft[i]);
    Serial.print(F(" stopAfterLow="));
    Serial.println((stopAfterLow & bit) ? 1 : 0);
  }
}

// -----------------------------
// Serial command handling
// -----------------------------
void handleCommand(String line) {
  line.trim();
  line.toUpperCase();

  if (line.length() == 0) return;

  String cmd = getToken(line, 0);

  if (cmd == "HELP") {
    printHelp();
    return;
  }

  if (cmd == "STATUS") {
    printStatus();
    return;
  }

  if (cmd == "MODEL") {
    String modeStr = getToken(line, 1);
    uint8_t mode;

    if (!parseUInt8(modeStr, mode) || mode > 1) {
      Serial.println(F("ERR MODEL must be 0 or 1"));
      return;
    }

    noInterrupts();
    pulseModel = mode;
    interrupts();

    for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
      recalcChannel(i);
    }

    Serial.print(F("OK MODEL "));
    Serial.println(mode);
    return;
  }

  if (cmd == "SET") {
    String chStr = getToken(line, 1);
    String rpmStr = getToken(line, 2);
    String dutyStr = getToken(line, 3);

    uint8_t channel;
    if (!parseUInt8(chStr, channel) || channel < 1 || channel > NUM_CHANNELS) {
      Serial.println(F("ERR invalid channel"));
      return;
    }

    float rpm = rpmStr.toFloat();
    float duty = dutyStr.toFloat();

    if (rpm < 1.0f || rpm > 50000.0f) {
      Serial.println(F("ERR rpm out of range"));
      return;
    }

    if (duty <= 0.0f || duty >= 100.0f) {
      Serial.println(F("ERR duty must be >0 and <100"));
      return;
    }

    uint8_t idx = channel - 1;
    setChannelConfig(idx, rpm, duty);

    Serial.print(F("OK SET CH "));
    Serial.print(channel);
    Serial.print(F(" RPM "));
    Serial.print(rpmCfg[idx], 1);
    Serial.print(F(" DUTY "));
    Serial.println(dutyCfg[idx], 1);
    return;
  }

  if (cmd == "SETMASK") {
    String maskStr = getToken(line, 1);
    String rpmStr = getToken(line, 2);
    String dutyStr = getToken(line, 3);
    uint8_t mask;

    if (!parseChannelMask(maskStr, mask)) {
      Serial.println(F("ERR invalid mask"));
      return;
    }

    float rpm = rpmStr.toFloat();
    float duty = dutyStr.toFloat();

    if (rpm < 1.0f || rpm > 50000.0f) {
      Serial.println(F("ERR rpm out of range"));
      return;
    }

    if (duty <= 0.0f || duty >= 100.0f) {
      Serial.println(F("ERR duty must be >0 and <100"));
      return;
    }

    setMaskConfig(mask, rpm, duty);

    Serial.print(F("OK SETMASK 0x"));
    Serial.print(mask, HEX);
    Serial.print(F(" RPM "));
    Serial.print(rpm, 1);
    Serial.print(F(" DUTY "));
    Serial.println(duty, 1);
    return;
  }

  if (cmd == "START") {
    String chStr = getToken(line, 1);
    uint8_t channel;

    if (!parseUInt8(chStr, channel) || channel < 1 || channel > NUM_CHANNELS) {
      Serial.println(F("ERR invalid channel"));
      return;
    }

    startChannel(channel - 1);

    Serial.print(F("OK START "));
    Serial.println(channel);
    return;
  }

  if (cmd == "STARTMASK") {
    String maskStr = getToken(line, 1);
    uint8_t mask;

    if (!parseChannelMask(maskStr, mask)) {
      Serial.println(F("ERR invalid mask"));
      return;
    }

    startMaskChannels(mask);

    Serial.print(F("OK STARTMASK 0x"));
    Serial.println(mask, HEX);
    return;
  }

  if (cmd == "RUN") {
    String chStr = getToken(line, 1);
    String pulsesStr = getToken(line, 2);
    uint8_t channel;
    uint32_t pulses;

    if (!parseUInt8(chStr, channel) || channel < 1 || channel > NUM_CHANNELS) {
      Serial.println(F("ERR invalid channel"));
      return;
    }

    if (!parseUInt32(pulsesStr, pulses) || pulses == 0) {
      Serial.println(F("ERR pulses must be a positive integer"));
      return;
    }

    runChannelForPulses(channel - 1, pulses);

    Serial.print(F("OK RUN "));
    Serial.print(channel);
    Serial.print(F(" PULSES "));
    Serial.println(pulses);
    return;
  }

  if (cmd == "RUNMASK") {
    String maskStr = getToken(line, 1);
    String pulsesStr = getToken(line, 2);
    uint8_t mask;
    uint32_t pulses;

    if (!parseChannelMask(maskStr, mask)) {
      Serial.println(F("ERR invalid mask"));
      return;
    }

    if (!parseUInt32(pulsesStr, pulses) || pulses == 0) {
      Serial.println(F("ERR pulses must be a positive integer"));
      return;
    }

    runMaskChannelsForPulses(mask, pulses);

    Serial.print(F("OK RUNMASK 0x"));
    Serial.print(mask, HEX);
    Serial.print(F(" PULSES "));
    Serial.println(pulses);
    return;
  }

  if (cmd == "STOP") {
    String chStr = getToken(line, 1);
    uint8_t channel;

    if (!parseUInt8(chStr, channel) || channel < 1 || channel > NUM_CHANNELS) {
      Serial.println(F("ERR invalid channel"));
      return;
    }

    stopChannel(channel - 1);

    Serial.print(F("OK STOP "));
    Serial.println(channel);
    return;
  }

  if (cmd == "STOPMASK") {
    String maskStr = getToken(line, 1);
    uint8_t mask;

    if (!parseChannelMask(maskStr, mask)) {
      Serial.println(F("ERR invalid mask"));
      return;
    }

    stopMaskChannels(mask);

    Serial.print(F("OK STOPMASK 0x"));
    Serial.println(mask, HEX);
    return;
  }

  if (cmd == "STARTALL") {
    startAllChannels();
    Serial.println(F("OK STARTALL"));
    return;
  }

  if (cmd == "STOPALL") {
    stopAllChannels();
    Serial.println(F("OK STOPALL"));
    return;
  }

  Serial.println(F("ERR unknown command"));
}

void serviceSerial() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();

    if (c == '\n' || c == '\r') {
      if (inputLine.length() > 0) {
        handleCommand(inputLine);
        inputLine = "";
      }
    } else {
      if (inputLine.length() < 120) {
        inputLine += c;
      }
    }
  }
}

// -----------------------------
// Setup / loop
// -----------------------------
void setup() {
  Serial.begin(115200);

  // D5-D7 on PORTD, D8 on PORTB as outputs
  DDRD |= OUTPUT_MASK_PORTD;
  DDRB |= OUTPUT_MASK_PORTB;
  forceOutputsOff(OUTPUT_MASK);

  // Defaults
  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    rpmCfg[i] = 1000.0f;
    dutyCfg[i] = 25.0f;
    onTicks[i] = 1;
    offTicks[i] = 1;
    ticksLeft[i] = 1;
  }

  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    recalcChannel(i);
  }

  setupTimer1();

  Serial.println(F("Injector mask-ISR controller ready"));
  printHelp();
}

void loop() {
  serviceSerial();
}
