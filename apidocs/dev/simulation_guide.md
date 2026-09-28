# Guide: Running the OpenFlexure Microscope Simulation Server

This guide walks you through running the simulation server for the OpenFlexure Microscope, along with useful tips for using the simulation features.

The simulation server GUI is a copy of the GUI you would find on the hardware with a simulated sample you can explore. Most functionality included in the hardware operates on the simulation server. This includes:

* Camera stage mapping and 'click to move'
* Scanning and stitching*
* Autofocus
* Image capture
* Stage navigation

**Please note: the final stitch at the end of a scan currently doesn't work on Windows.*

---

## Starting the Simulation Server

1. **Activate your virtual environment** (the same one created during installation, as described in the [Python documentation](python)).
2. **Run the simulation server** using the following command:

   ```bash
   openflexure-microscope-server --fallback -c ./ofm_config_simulation.json
   ```

   > Note: If you are using Windows, we advise running this command in PowerShell. If it doesn't work this, might be a permission issue on managed machines, in which case try executing the command in a CMD terminal.

This can fail with an error that the chosen port is already in use. In that case, append `--port 8081`

> Note: If you would like more detailed logging information from both the OpenFlexure Microscope server and the LabThings server, you can add the `--debug` argument to the end of the above command to activate `DEBUG` level logs.

---

## Video Walkthrough

For a step-by-step demonstration, watch the [full video walkthrough on running the simulation server](https://youtu.be/Z8verCqfj9s).

---

## Running a simulated scan

To run a simulated scan, you must first complete the calibration wizard.

1. Go to the  **Slide Scan** tab
2. Click the **Start Smart Scan** Button.
3. This will scan until the whole sample has been imaged. You can cancel the scan at any time, with the **Cancel** button.

---

## Simulation Settings and Tips

---

### Auto-calibration

* Your simulated microscope should prompt you to complete the auto-calibration wizard upon your first use of the simulated server.
* You can press the **Escape** key at any point to exit the wizard, but this will leave your simulated microscope in an uncalibrated state.
* If your microscope is not calibrated, the wizard will launch each time you load the UI, prompting you to complete remaining calibrations.
* You can always launch the full calibration wizard by going to: **Settings → Launch Calibration Wizard**.

The calibration steps are:

1. The Camera Calibration will set the background image needed for background detect.
  * You can rerun this calibration by going to: **Settings → Camera → Full Auto-Calibrate**.
2. The Camera-Stage Mapping calibration is used to find the relationship between stage and image coordinates.
  * If the stage moves in the wrong direction when double clicking on the camera feed in the **View** or **Navigate** tabs, you may need to re-run Camera-Stage Mapping.
  * To re-run Camera-Stage Mapping, go to: **Settings → Camera to Stage Mapping → Auto-Calibrate Using Camera**.


---

### Background Detect

* You can **load a sample** to simulate imaging conditions, using the **Load Sample** options in the **Settings tab**.
* To remove a sample and reset the view, use the **Remove Sample** option in the **Settings tab**.
* You can load and remove simulated samples under: **Settings → Camera → Load/Remove Sample**.
* The Full Auto-Calibrate option in camera settings can be used to **Remove the sample → Run background detection → Re-load the sample**.
* You can adjust the sample coverage needed for the image not to be detected as background in the **Background Detect tab** under: **Configure → Sample Coverage**. The default value is 25%.

---

### Scans

* If you perform a scan with **0 images** (i.e. cancelling the scan before any images are collected), it will **disappear** from the scan list as soon as a new scan starts.
* Scans are automatically given **Scan IDs** in numerical order if the user does not provide one.

---

If you need further help, reach out to the community on the [OpenFlexure Forum](https://openflexure.discourse.group/).

