# Smart AndonLedger AI

Smart AndonLedger AI is a vision-based IIoT monitoring application for Arduino UNO Q.

## Setup

1. Open **Arduino App Lab**.
2. Create or copy an application that uses:
   - Video Object Detection
   - WebUI
3. Select a compatible object-detection model in the Video Object Detection brick.
4. Copy these project files into the App Lab application:

```text
python/
    main.py

assets/
    index.html
    app.js
    style.css
```

5. Keep the App Lab-generated files and libraries already present in the project.
6. Connect a USB camera to the Arduino UNO Q.
7. Run the application from App Lab.
8. Open the WebUI dashboard in your browser.

## Stack-Light Model

For the complete Smart AndonLedger application, the object-detection model should contain these classes:

```text
red
yellow
green
```

The application interprets them as:

```text
Green  -> RUNNING
Yellow -> WARNING
Red    -> FAULT
```

If no reliable stack-light detection is available, the machine state becomes:

```text
UNKNOWN
```

## Event Log

Confirmed machine-state transitions are stored locally in:

```text
data/andonledger_events.csv
```

The CSV file records:

```text
timestamp_utc
old_state
new_state
active_lights
max_confidence
```

## Dashboard

The WebUI displays:

- Live camera feed
- Machine state
- Active lights
- Detection confidence
- Runtime
- Downtime
- Warning time
- Fault count
- Warning count
- Availability
- Unknown time
- Prolonged-stop alert
- Recent event history

## Run

Connect the USB camera, start the application in Arduino App Lab, and open the WebUI dashboard.

The Arduino UNO Q performs the object detection and machine-state processing locally.
