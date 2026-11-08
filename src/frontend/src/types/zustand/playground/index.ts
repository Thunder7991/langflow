export type PlaygroundStoreType = {
  requestPlatformStop: (() => void) | null;
  setRequestPlatformStop: (stop: (() => void) | null) => void;
  isPlayground: boolean;
  setIsPlayground: (isPlayground: boolean) => void;
  isFullscreen: boolean;
  toggleFullscreen: () => void;
  setIsFullscreen: (isFullscreen: boolean) => void;
  isOpen: boolean;
  setIsOpen: (isOpen: boolean) => void;
  reset: () => void;
};
