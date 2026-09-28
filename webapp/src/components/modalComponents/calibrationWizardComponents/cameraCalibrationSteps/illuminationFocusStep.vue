<template>
  <stepTemplateWithStream>
    <div>
      <p>Adjust the illumination focus:</p>
      <ul class="uk-list uk-list-bullet">
        <li>Loosen the thumbscrew on the condenser.</li>
        <li>
          Carefully move the condenser up / down slightly, until the stream below is as bright as
          possible.
        </li>
        <li>Tighten the thumbscrew.</li>
      </ul>
    </div>
  </stepTemplateWithStream>
</template>

<script>
import stepTemplateWithStream from "../stepTemplateWithStream.vue";
export default {
  name: "IlluminationFocusStep",

  components: {
    stepTemplateWithStream,
  },
};
</script>
<style scoped></style>
