/*
  Uno R3 injector pulse controller - mask-based ISR version
  ---------------------------------------------------------
  IMPORTANT:
  - Do NOT connect fuel injectors directly to Arduino pins.
  - Use proper low-side drivers / MOSFETs and flyback/clamp protection.
  - Use an external supply for injectors.
  - Share ground between Arduino and injector power supply.

  Outputs:
    CH1 -> D4
    CH2 -> D5
    CH3 -> D6
    CH4 -> D7

  Serial commands:
    HELP
    STATUS
    MODEL <0|1>
    SET <channel> <rpm> <dutyPercent>
    START <channel>
    STOP <channel>
    STARTALL
    STOPALL

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

// D4..D7 on PORTD
static const uint8_t CH1_BIT = _BV(PD4);
static const uint8_t CH2_BIT = _BV(PD5);
static const uint8_t CH3_BIT = _BV(PD6);
static const uint8_t CH4_BIT = _BV(PD7);
static const uint8_t OUTPUT_MASK = CH1_BIT | CH2_BIT | CH3_BIT | CH4_BIT;

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
volatile uint8_t stateMask  = 0;   // channels currently HIGH

volatile uint16_t onTicks[NUM_CHANNELS];
volatile uint16_t offTicks[NUM_CHANNELS];
volatile uint16_t ticksLeft[NUM_CHANNELS];

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

// -----------------------------
// Channel control helpers
// -----------------------------
void forceOutputsLow(uint8_t mask) {
  PORTD &= ~mask;
}

void startChannel(uint8_t idx) {
  if (idx >= NUM_CHANNELS) return;

  uint8_t bit = channelBits[idx];

  noInterrupts();
  activeMask |= bit;
  stateMask &= ~bit;             // force known LOW state
  ticksLeft[idx] = offTicks[idx]; // begin with OFF delay, then first ON edge
  interrupts();

  PORTD &= ~bit;
}

void stopChannel(uint8_t idx) {
  if (idx >= NUM_CHANNELS) return;

  uint8_t bit = channelBits[idx];

  noInterrupts();
  activeMask &= ~bit;
  stateMask &= ~bit;
  interrupts();

  PORTD &= ~bit;
}

void startAllChannels() {
  noInterrupts();
  activeMask = OUTPUT_MASK;
  stateMask &= ~OUTPUT_MASK;

  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    ticksLeft[i] = offTicks[i];
  }
  interrupts();

  PORTD &= ~OUTPUT_MASK;
}

void stopAllChannels() {
  noInterrupts();
  activeMask &= ~OUTPUT_MASK;
  stateMask &= ~OUTPUT_MASK;
  interrupts();

  PORTD &= ~OUTPUT_MASK;
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
        ticksLeft[0] = offTicks[0];
      } else {
        stateMask |= CH1_BIT;
        setMask |= CH1_BIT;
        ticksLeft[0] = onTicks[0];
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
        ticksLeft[1] = offTicks[1];
      } else {
        stateMask |= CH2_BIT;
        setMask |= CH2_BIT;
        ticksLeft[1] = onTicks[1];
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
        ticksLeft[2] = offTicks[2];
      } else {
        stateMask |= CH3_BIT;
        setMask |= CH3_BIT;
        ticksLeft[2] = onTicks[2];
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
        ticksLeft[3] = offTicks[3];
      } else {
        stateMask |= CH4_BIT;
        setMask |= CH4_BIT;
        ticksLeft[3] = onTicks[3];
      }
    }
  }

  // Single port commit
  uint8_t pd = PORTD;
  pd |= setMask;
  pd &= ~clearMask;
  PORTD = pd;
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
  Serial.println(F("  START <channel 1-4>"));
  Serial.println(F("  STOP <channel 1-4>"));
  Serial.println(F("  STARTALL"));
  Serial.println(F("  STOPALL"));
  Serial.println(F(""));
  Serial.println(F("Models:"));
  Serial.println(F("  0 = 4-stroke, 1 event per 2 revs (Hz = RPM/120)"));
  Serial.println(F("  1 = 1 event per rev            (Hz = RPM/60)"));
}

void printStatus() {
  uint8_t active, state, model;
  uint16_t onT[NUM_CHANNELS], offT[NUM_CHANNELS], leftT[NUM_CHANNELS];

  noInterrupts();
  active = activeMask;
  state = stateMask;
  model = pulseModel;
  for (uint8_t i = 0; i < NUM_CHANNELS; i++) {
    onT[i] = onTicks[i];
    offT[i] = offTicks[i];
    leftT[i] = ticksLeft[i];
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
    Serial.print(F(" rpm="));
    Serial.print(rpmCfg[i], 1);
    Serial.print(F(" duty="));
    Serial.print(dutyCfg[i], 1);
    Serial.print(F(" onTicks="));
    Serial.print(onT[i]);
    Serial.print(F(" offTicks="));
    Serial.print(offT[i]);
    Serial.print(F(" ticksLeft="));
    Serial.println(leftT[i]);
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
    rpmCfg[idx] = rpm;
    dutyCfg[idx] = duty;
    recalcChannel(idx);

    Serial.print(F("OK SET CH "));
    Serial.print(channel);
    Serial.print(F(" RPM "));
    Serial.print(rpmCfg[idx], 1);
    Serial.print(F(" DUTY "));
    Serial.println(dutyCfg[idx], 1);
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

  // D4-D7 as outputs
  DDRD |= OUTPUT_MASK;
  PORTD &= ~OUTPUT_MASK;

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