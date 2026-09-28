# Raspberry Pi Camera Tuning Files

The OpenFlexure Microscope ships its own custom Raspberry Pi Camera tuning files. This page provides more information on these files.


## A list of all the settings in the Raspberry Pi Camera (vc4) tuning files.

Note that VC4 is the patform used by the Pi for with the broadcom ISP. From Pi 5 onwards Raspberry Pi uses their own ISP (PiSP). These have their own tuning file structure.

More data is available in this datasheet: https://datasheets.raspberrypi.com/camera/raspberry-pi-camera-guide.pdf

### version:

This needs to be 2.0. Version one was a normal dictionary of algorithms, rather than this current structure with the algorithms as a list.

### target

Needs to be bcm2835. This is the broadcom system on a chip used by vc4.

### Algorithms

#### rpi.black_level - Sensor Black Level

Contains one key-value pair. i.e.: `black_level: 4096`

Note that this is not the actual black level of the sensor, but the black level once the image has been scaled up to 16 bit. So for a 10-bit sensor this number would need to be divided by 2^6.

#### rpi.dpc - Defective Pixel Correction

This has three strength parameters:

*  0 - None
*  1 - Normal (default)
*  2 - Strong

#### rpi.lux - Lux Calculation

This is used to create the lux value. This lux value is not used in the ISP but is sent to the metadata for the image for downstream control algorithms. The camera may crash if this is removed even though we turn off the adaptive algorithms.

#### rpi.noise - Noise Profile

This calculates the noise profile for the current images.

Values from the default tuning files for imx219 and imx477 are:

```
"reference_constant": 0,
"reference_slope": 3.67
```

These can be optimised using the methods in the camera tuning tool shipped with libcamera. Though most of the camera tuning tool methods make assumptions that are invalid for a microscope. As such any tuning would need further consideration.

#### rpi.geq - Green Equalisation

##### The problems with green equalisation

This tried to calculate whether adjacent grren pixels should be equal. This interacts very porly with the chief ray angle compensation when using the camera. The imx219 has a lenslet array:

![](assets/ChiefRayAngle.png)

Normally as shown in (a) the light comes in at an angle as we get to the edge of the sensor due to how close the lens is to the camera. The lenslet array is designed for this and so if you are in the centre (b), or at the edge (d) the light is focussed onto the correct pixel.

For a microscope the light comes in perpendicular to the sensor, this makes no difference in the centre but at the edge of the image (e) there is a lot of cross talk between pixels.

But it makes sense that this also strongly affects the green imbalance (which is something I didn't know about, but should, and will need to read about in more detail so we can improve tuning). If we consider the case of red light, we expect that the red pixels are the only illuminated pixels. If we consider what happens on the sensor in the case of the microscope, I think the light will be focussing on the positions shown:

![](assets/GreenEqualisation.png)

So in the case of the diagonals this is illuminating the green in both the red and the blue row. Where as in the areas we were seeing issues the red light will be strongly illuminating the the greens in one row but not the other.

The result is that for strongly red on the edges every other geen pixel is illuminated causing an overflow.

##### Turning off the adaptive green equalisation

The parameters for geeen equalisation are a `slope` and `offset`

The "green equalisation" algorithm averages Gr and Gb when they "don't look that different". The threshold below which things get averaged is given by

`threshold = offset + pixel_value * slope`

As we always want the green pixels to be averaged to remove the effect above. We can set `offset` the maximum 16-bit value (65535). In this case it will always average green pixels, no matter the pixel value or the value of `slope`.

#### rpi.sdn - Spatial Denoise

This algorithm is used on the VC4 (Pi 4) but not on PiSP (Pi 5) which uses `rpi.denoise`. A warning on startup asks us to move it into `rpi.denoise` even though we are on VC4 that does not support `rpi.denoise`.

#### rpi.awb - Automatic White Balance

Even though we don't use it, camera can't start without it being minimally populated.

#### rpi.agc - Automatic Gain Control / Automatic Exposure Control

For calculating the shutter time of the sensor if AeEnable is on. Only the required fields are populated with minimal data as AeEnable is always off.

#### rpi.alsc - Automatic Lens Shading Correction

Auto lens shading correction is calibrated by our microscope. This contains the tables for luninance, and colour shading. It can contain multiple tables depending on estimated colour temperature (`ct`). As the microscope has fixed lighting  we only provide one set of tables as we don't want the correction to be adaptive.

We also set `n_iter: 0`. This stops it adding iterative adaptuion.

As there is only one table, the colour temperature is ignored. We set the colour temperature to 1234 in the tuning file to make it clear that we have not calibrated it on the microscope. We set the colour temperature to 5000 (our actual illumination colour temperature) once recalibrated.

The tables are flat 1s for luminance on for both sensors.

For the IMX477 (PiCamera HQ), the Cr and Cb tables are flat, but Cb is 2.0 not 1.0 for all values. This is so that the initial colour gains are set to (1.0, 2.0)

For the IMX219 (PiCamera v2), the Cr and Cb tables are symmetric tables taken from the top quadrant of a calibrated microscope. They are used to create a good enough correction that the microscope is initially usable for alignment.

#### rpi.contrast - Contrast

This does the gamma correction. If `ce_enable` is 1 then the gamma correction is somewhat adaptive. We set this to zero to always use the specified gamma table.

The table of gamma values is a 1d list of alternating x,y values in the gamma curve.

We may want to adjust the gamma curve in future.

#### rpi.ccm - Colour Correction Matrix

It is possible tp ship different CCMs to be used based on the estimated colour temperatire. We only ship one CCM as our illumination colour temperature is fixed to 5000K, the colour temperature of the LED board. However, the diffuser might affect this.

The CCMs are different for PiCamera HQ and PiCamera version 2. The default tuning files for these cameras (`/usr/share/libcamera/ipa/rpi/vc4/imx219.json` or `/usr/share/libcamera/ipa/rpi/vc4/imx477.json`) specify CCMs for a number of temperatures but not for 5000K. As such we linearly interpolate between temperatures around 5000K with the following code:

```
import numpy as np

temps = {4650:
        np.array([
            2.18174, -0.70887, -0.47287,
            -0.70196, 2.76426, -1.06231,
            -0.25157, -0.71978, 1.97135
        ]),
        5858:
        np.array(
        [
                2.32392, -0.88421, -0.43971,
                -0.63821, 2.58348, -0.94527,
                -0.28541, -0.54112, 1.82653
        ])
    }
target_temp = 5000
temp_low, temp_high = sorted(temps.keys())
alpha = (target_temp - temp_low) / (temp_high - temp_low)

print(alpha)

ccm_interp = (1 - alpha) * temps[temp_low] + alpha * temps[temp_high]
print(np.round(ccm_interp, 6))

temps = {4559: np.array(
                        [
                            2.15423, -0.98143, -0.17279,
                            -0.38131, 2.14763, -0.76632,
                            -0.10069, -0.54383, 1.64452
                        ]),
        5881:np.array(
            [
                2.18464, -0.95493, -0.22971,
                -0.36826, 2.00298, -0.63471,
                -0.15219, -0.38055, 1.53274
            ])
    }
target_temp = 5000
temp_low, temp_high = sorted(temps.keys())
alpha = (target_temp - temp_low) / (temp_high - temp_low)

print(alpha)

ccm_interp = (1 - alpha) * temps[temp_low] + alpha * temps[temp_high]
print(np.round(ccm_interp, 6))
```

##### Calculating our own CCMs

The CCM calculation is done using a MacBeth target. Lib camera provides a method for this using the Colour Tuning Tool. We will need to adapt this algorithm as they image the target at once and pick out the areas. We have fixed lighting but do not have colour uniformity.

In the future, with a colour calibration slide, we can take images of each colour with fixed camera settings to create a CCM using the same calculations as the Camera Tuning Tool.

#### rpi.sharpen - Sharpening

This is empty. We do not want to be artificially sharpening images as this can add in artifacts.

#### rpi.hdr - High dynamic range

Not relevant for us, we don't use high dynamic range capture.

#### rpi.sync - Software Camera Synchronisation

Not relevant for us, we only have 1 camera.
