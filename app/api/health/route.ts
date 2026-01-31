import { NextResponse } from 'next/server';
import intel from '../../../data/intel_snapshot.json';
import type { OverallStatus } from '../../../types/intel';

const STALE_THRESHOLD_HOURS = 24;

// Define a minimal type for the imported JSON
interface IntelData {
  last_updated: string;
  version?: string;
  status?: OverallStatus;
  macro?: { status?: string; rag_score?: string };
  peers?: { status?: string; rag_score?: string };
  suppliers?: { status?: string; rag_score?: string };
  health?: {
    errors_count?: number;
    warnings_count?: number;
    circuit_breaker_state?: string;
  };
}

interface HealthResponse {
  status: 'healthy' | 'stale' | 'degraded' | 'unknown';
  timestamp: string;
  data: {
    last_updated: string;
    version: string;
    age_hours: number;
    is_stale: boolean;
    overall_status: OverallStatus;
  };
  pillars: {
    macro: string;
    peers: string;
    suppliers: string;
  };
  health?: {
    errors_count: number;
    warnings_count: number;
    circuit_breaker_state: string;
  };
}

export async function GET() {
  try {
    const typedIntel = intel as IntelData;
    const lastUpdate = new Date(typedIntel.last_updated);
    const now = new Date();
    const ageHours = (now.getTime() - lastUpdate.getTime()) / (1000 * 60 * 60);
    const isStale = ageHours > STALE_THRESHOLD_HOURS;

    // Determine overall health status
    let healthStatus: 'healthy' | 'stale' | 'degraded' | 'unknown';

    if (typedIntel.status === 'degraded' || typedIntel.status === 'fallback') {
      healthStatus = 'degraded';
    } else if (isStale) {
      healthStatus = 'stale';
    } else if (
      typedIntel.status === 'healthy' ||
      typedIntel.status === 'partial'
    ) {
      healthStatus = 'healthy';
    } else {
      healthStatus = 'unknown';
    }

    const response: HealthResponse = {
      status: healthStatus,
      timestamp: now.toISOString(),
      data: {
        last_updated: typedIntel.last_updated,
        version: typedIntel.version || 'unknown',
        age_hours: Math.round(ageHours * 10) / 10,
        is_stale: isStale,
        overall_status: (typedIntel.status as OverallStatus) || 'degraded',
      },
      pillars: {
        macro: typedIntel.macro?.status || 'unknown',
        peers: typedIntel.peers?.status || 'unknown',
        suppliers: typedIntel.suppliers?.status || 'unknown',
      },
    };

    // Include health details if available
    if (typedIntel.health) {
      response.health = {
        errors_count: typedIntel.health.errors_count || 0,
        warnings_count: typedIntel.health.warnings_count || 0,
        circuit_breaker_state: typedIntel.health.circuit_breaker_state || 'unknown',
      };
    }

    // Return with appropriate status code
    const statusCode = healthStatus === 'healthy' ? 200 : healthStatus === 'stale' ? 200 : 503;

    return NextResponse.json(response, { status: statusCode });
  } catch (error) {
    return NextResponse.json(
      {
        status: 'error',
        timestamp: new Date().toISOString(),
        error: error instanceof Error ? error.message : 'Unknown error',
      },
      { status: 500 }
    );
  }
}
