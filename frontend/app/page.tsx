"use client";

import {App} from "./App";
import {PrefsProvider} from "./i18n";

export default function HomePage() {
  return (
    <PrefsProvider>
      <App />
    </PrefsProvider>
  );
}
