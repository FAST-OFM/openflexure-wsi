<template>
  <div>
    <p>Camera-stage mapping will calibrate the stage movement using the camera.</p>
    <p>
      <b> Before starting camera-stage mapping: </b>
    </p>
    <ul class="uk-list uk-list-bullet">
      <li>Add a sample with dense features to the microscope.</li>
      <li>Ensure the sample is reasonably well centered on the microscope.</li>
    </ul>
    <img src="/calibration_images/view-sample.svg" alt="View sample" />
  </div>
</template>

<script>
export default {
  name: "CsmExplanation",
};
</script>
