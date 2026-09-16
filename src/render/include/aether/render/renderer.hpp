#pragma once
#include <memory>
#include <string>

#include "aether/core/image.hpp"
#include "aether/core/render_types.hpp"
#include "aether/core/resources.hpp"
#include "aether/core/frame_state.hpp"

namespace aether::render {

class IRenderer {
public:
    virtual ~IRenderer() = default;
    virtual std::string name() const = 0;
    // Returns a linear HDR RGBA image (alpha = coverage over background).
    virtual Image render(const FrameState& state, const ResourceSet& resources, const CameraDesc& camera,
                         const RenderSettings& settings) = 0;
    virtual RenderStatistics last_statistics() const = 0;
};

// Reference CPU renderer. See docs/ARCHITECTURE.md section 7.
std::unique_ptr<IRenderer> create_software_renderer();

}  // namespace aether::render
