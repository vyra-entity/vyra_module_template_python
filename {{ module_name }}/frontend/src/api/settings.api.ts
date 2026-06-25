/**
 * API client for {{ module_name }} settings.
 */

const API_BASE = '/{{ module_name }}/api'

export interface ModulePermissionsResponse {
  success: boolean
  permissions: Record<string, unknown>
}

export interface ModuleAboutInfo {
  name?: string
  display_name?: string
  version?: string
  description?: string
  author?: string
  blueprints?: string
  uuid?: string
}

export interface ModuleAboutResponse {
  success: boolean
  module: ModuleAboutInfo
}

class SettingsApi {
  private async _errorMessage(response: Response, fallback: string): Promise<string> {
    const ct = response.headers.get('content-type') ?? ''
    if (ct.includes('application/json')) {
      try {
        const body = await response.json()
        return body.detail || fallback
      } catch {
        // Ignore parse errors and use fallback.
      }
    }
    return `${fallback} (HTTP ${response.status})`
  }

  async getModulePermissions(): Promise<ModulePermissionsResponse> {
    const response = await fetch(`${API_BASE}/settings/permissions`, {
      credentials: 'include',
    })
    if (!response.ok) {
      throw new Error(await this._errorMessage(response, 'Failed to load module permissions'))
    }
    return response.json()
  }

  async updateModulePermissions(permissions: Record<string, unknown>): Promise<ModulePermissionsResponse> {
    const response = await fetch(`${API_BASE}/settings/permissions`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ permissions }),
    })
    if (!response.ok) {
      throw new Error(await this._errorMessage(response, 'Failed to save module permissions'))
    }
    return response.json()
  }

  async getModuleAbout(): Promise<ModuleAboutResponse> {
    const response = await fetch(`${API_BASE}/settings/about`, {
      credentials: 'include',
    })
    if (!response.ok) {
      throw new Error(await this._errorMessage(response, 'Failed to load module information'))
    }
    return response.json()
  }
}

export const settingsApi = new SettingsApi()
