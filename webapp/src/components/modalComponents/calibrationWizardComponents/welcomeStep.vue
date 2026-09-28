<template>
  <div>
    <h4 class="cal-missing">Some important microscope calibration data is currently missing.</h4>
    <p>
      Your microscope will still function, however some functionality will be limited, and image
      quality will suffer.
    </p>
    <p>
      <b>Click Next to begin microscope calibration.</b>
    </p>
  </div>
</template>

<script>
export default {
  name: "WelcomeStep",
};
</script>

<style scoped>
.cal-missing {
  color: #f0506e;
  font-weight: 600;
}
</style>
