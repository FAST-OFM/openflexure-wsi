import js from "@eslint/js";
import vue from "eslint-plugin-vue";
import prettierPlugin from "eslint-plugin-prettier";
import prettierConfig from "eslint-config-prettier";
import globals from "globals";
import pinia from "eslint-plugin-pinia";

const isDev = process.env.NODE_ENV === "development";

export default [
  {
    ignores: [
      "dist/**/*",
      "lib/**/*",
      "**/*.min.js",
      "tools/architecture_dashboard/**/*",
      "coverage/",
    ],
  },

  js.configs.recommended,

  ...vue.configs["flat/recommended"],

  {
    files: ["**/*.vue", "**/*.js", "**/*.jsx", "**/*.cjs", "**/*.mjs", "**/*.spec.js"],
    languageOptions: {
      ecmaVersion: 2021,
      sourceType: "module",
      globals: {
        ...globals.node,
        ...globals.browser,
        ...globals.es2021,
        process: "readonly",
      },
    },

    plugins: {
      vue,
      prettier: prettierPlugin,
      pinia,
    },

    rules: {
      "prettier/prettier": "warn",
      // Environment-based rules
      "no-console": isDev ? "off" : ["warn", { allow: ["warn", "error"] }],
      "no-debugger": isDev ? "off" : "warn",

      // Vue deprecations
      "vue/no-deprecated-slot-attribute": "warn",
      "vue/no-deprecated-v-on-native-modifier": "warn",
      "vue/no-deprecated-dollar-listeners-api": "warn",
      "vue/no-deprecated-destroyed-lifecycle": "warn",
      "vue/no-deprecated-dollar-scopedslots-api": "warn",
      "vue/no-deprecated-events-api": "warn",
      "vue/no-deprecated-filter": "warn",
      "vue/no-deprecated-functional-template": "warn",
      "vue/no-deprecated-html-element-is": "warn",
      "vue/no-deprecated-inline-template": "warn",
      "vue/no-deprecated-props-default-this": "warn",
      "vue/no-deprecated-scope-attribute": "warn",
      "vue/no-deprecated-v-bind-sync": "warn",
      "vue/no-deprecated-v-is": "warn",
      "vue/no-lifecycle-after-await": "warn",
      "vue/no-watch-after-await": "warn",

      // Vue best practices
      "vue/require-explicit-emits": "warn",
      "vue/multi-word-component-names": "warn",
      "vue/no-v-for-template-key-on-child": "error",
      "vue/no-v-model-argument": "off",

      "vue/no-unused-components": "warn",
      "vue/no-unused-vars": "warn",

      // Pinia best practices
      "pinia/prefer-use-store-naming-convention": "warn",
      "pinia/require-setup-store-properties-export": "warn",
    },
  },

  {
    files: ["src/tests/unit/**/*.spec.js"],
    languageOptions: {
      globals: {
        describe: "readonly",
        it: "readonly",
        expect: "readonly",
        vi: "readonly",
        beforeEach: "readonly",
        afterEach: "readonly",
        mockLogData: "readonly", // Prevents unused global mock variable warnings
        afterAll: "readonly",
      },
    },
  },

  prettierConfig,
];
