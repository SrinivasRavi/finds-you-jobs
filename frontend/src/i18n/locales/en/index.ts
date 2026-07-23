// English — assembled from per-namespace slices so extraction work can land
// file-by-file. This object's shape is the contract every locale mirrors.
import analytics from "./analytics";
import common from "./common";
import jobBoard from "./jobBoard";
import networking from "./networking";
import onboarding from "./onboarding";
import popups from "./popups";
import settingsPage from "./settingsPage";
import shell from "./shell";
import tracker from "./tracker";

const en = {
  ...common,
  onboarding,
  jobBoard,
  tracker,
  networking,
  analytics,
  settingsPage,
  popups,
  shell,
};

export default en;
