import coreWebVitals from "eslint-config-next/core-web-vitals";
import security from "eslint-plugin-security";
import noUnsanitized from "eslint-plugin-no-unsanitized";

export default [
  ...coreWebVitals,
  security.configs.recommended,
  noUnsanitized.configs.recommended,
];
