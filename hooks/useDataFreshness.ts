'use client';

import { useState, useEffect, useCallback } from 'react';

interface DataFreshnessState {
  isStale: boolean;
  hoursSinceUpdate: number;
  lastUpdate: Date | null;
  version: string | null;
  hasNewVersion: boolean;
  isChecking: boolean;
  lastCheckTime: Date | null;
  error: string | null;
}

interface UseDataFreshnessOptions {
  /** Current version from loaded data */
  currentVersion?: string;
  /** Current last_updated timestamp from loaded data */
  lastUpdated?: string;
  /** Interval in ms between freshness checks (default: 5 minutes) */
  checkInterval?: number;
  /** Threshold in hours after which data is considered stale (default: 24) */
  staleThresholdHours?: number;
  /** Whether to automatically check for freshness (default: true) */
  autoCheck?: boolean;
}

/**
 * Hook to track data freshness and check for updates
 *
 * @example
 * const { isStale, hasNewVersion, checkForUpdates, refreshData } = useDataFreshness({
 *   currentVersion: intel.version,
 *   lastUpdated: intel.last_updated,
 * });
 */
export function useDataFreshness(options: UseDataFreshnessOptions = {}) {
  const {
    currentVersion,
    lastUpdated,
    checkInterval = 5 * 60 * 1000, // 5 minutes
    staleThresholdHours = 24,
    autoCheck = true,
  } = options;

  const [state, setState] = useState<DataFreshnessState>(() => {
    const lastUpdate = lastUpdated ? new Date(lastUpdated) : null;
    const now = new Date();
    const hoursSinceUpdate = lastUpdate
      ? (now.getTime() - lastUpdate.getTime()) / (1000 * 60 * 60)
      : Infinity;

    return {
      isStale: hoursSinceUpdate > staleThresholdHours,
      hoursSinceUpdate,
      lastUpdate,
      version: currentVersion || null,
      hasNewVersion: false,
      isChecking: false,
      lastCheckTime: null,
      error: null,
    };
  });

  /**
   * Check if there's a new version of the data available
   */
  const checkForUpdates = useCallback(async (): Promise<boolean> => {
    if (!currentVersion) {
      return false;
    }

    setState((prev) => ({ ...prev, isChecking: true, error: null }));

    try {
      // Fetch the JSON file with cache-busting
      const response = await fetch(
        `/data/intel_snapshot.json?_t=${Date.now()}`,
        {
          cache: 'no-store',
        }
      );

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }

      const data = await response.json();
      const newVersion = data.version;
      const hasNewVersion = newVersion !== currentVersion;

      setState((prev) => ({
        ...prev,
        isChecking: false,
        lastCheckTime: new Date(),
        hasNewVersion,
        error: null,
      }));

      return hasNewVersion;
    } catch (error) {
      const errorMessage =
        error instanceof Error ? error.message : 'Unknown error';
      setState((prev) => ({
        ...prev,
        isChecking: false,
        lastCheckTime: new Date(),
        error: errorMessage,
      }));
      return false;
    }
  }, [currentVersion]);

  /**
   * Refresh the page to load new data
   */
  const refreshData = useCallback(() => {
    window.location.reload();
  }, []);

  /**
   * Dismiss the "new version available" notification
   */
  const dismissNewVersion = useCallback(() => {
    setState((prev) => ({ ...prev, hasNewVersion: false }));
  }, []);

  // Auto-check for updates at interval
  useEffect(() => {
    if (!autoCheck || !currentVersion) {
      return;
    }

    // Initial check after 30 seconds
    const initialTimeout = setTimeout(() => {
      checkForUpdates();
    }, 30 * 1000);

    // Regular interval checks
    const intervalId = setInterval(() => {
      checkForUpdates();
    }, checkInterval);

    return () => {
      clearTimeout(initialTimeout);
      clearInterval(intervalId);
    };
  }, [autoCheck, checkInterval, checkForUpdates, currentVersion]);

  // Update staleness calculation when lastUpdated changes
  useEffect(() => {
    const updateStaleness = () => {
      const lastUpdate = lastUpdated ? new Date(lastUpdated) : null;
      const now = new Date();
      const hoursSinceUpdate = lastUpdate
        ? (now.getTime() - lastUpdate.getTime()) / (1000 * 60 * 60)
        : Infinity;

      setState((prev) => ({
        ...prev,
        isStale: hoursSinceUpdate > staleThresholdHours,
        hoursSinceUpdate,
        lastUpdate,
      }));
    };

    updateStaleness();

    // Update staleness every minute
    const intervalId = setInterval(updateStaleness, 60 * 1000);

    return () => clearInterval(intervalId);
  }, [lastUpdated, staleThresholdHours]);

  return {
    ...state,
    checkForUpdates,
    refreshData,
    dismissNewVersion,
  };
}

export default useDataFreshness;
