// Value typing/coercion, the JSON encoding from docs/VOCABULARY.md and
// keyframe tracks over effect time.
#include <cmath>
#include <string>
#include <vector>

#include <catch2/catch_test_macros.hpp>
#include <catch2/matchers/catch_matchers_floating_point.hpp>

#include "aether/core/error.hpp"
#include "aether/core/value.hpp"

using namespace aether;
using Catch::Matchers::WithinAbs;

namespace {

std::vector<std::pair<ValueType, Value>> sample_values() {
    return {
        {ValueType::Bool, Value(true)},
        {ValueType::Int, Value(-17)},
        {ValueType::Float, Value(0.09f)},
        {ValueType::Vec2, Value(Vec2{1.5f, -2.25f})},
        {ValueType::Vec3, Value(Vec3{0.1f, 2.0f, -3.75f})},
        {ValueType::Vec4, Value(Vec4{1.0f, 2.0f, 3.0f, 4.0f})},
        {ValueType::Color, Value(Color{1.0f, 0.55f, 0.15f, 0.5f})},
        {ValueType::String, Value(std::string("hello world"))},
        {ValueType::Enum, Value(std::string("additive"))},
        {ValueType::Ref, Value(std::string("flame_ps"))},
        {ValueType::Curve, Value(Curve{{0.0f, 0.0f, Interp::Smooth}, {0.5f, 1.0f, Interp::Step}, {1.0f, 0.25f, Interp::Linear}})},
        {ValueType::Gradient, Value(Gradient{{0.0f, Color{1, 0, 0, 1}}, {1.0f, Color{0, 0.5f, 1, 0.25f}}})},
        {ValueType::FloatList, Value(std::vector<float>{0.0f, 0.5f, 2.25f})},
        {ValueType::Vec3List, Value(std::vector<Vec3>{{0, 0, 0}, {0, 1, 0}, {1.5f, -1, 0.25f}})},
        {ValueType::Json, Value(nlohmann::json{{"nodes", nlohmann::json::array()}, {"output", "n1"}})},
    };
}

}  // namespace

TEST_CASE("value type names round trip", "[core][value]") {
    for (int i = 0; i <= static_cast<int>(ValueType::Json); ++i) {
        ValueType t = static_cast<ValueType>(i);
        ValueType back{};
        CAPTURE(to_string(t));
        REQUIRE(parse_value_type(to_string(t), back));
        CHECK(back == t);
    }
    ValueType unused{};
    CHECK_FALSE(parse_value_type("quaternion", unused));
    CHECK(to_string(ValueType::FloatList) == "float_list");
    CHECK(to_string(ValueType::Vec3List) == "vec3_list");
}

TEST_CASE("value_type_of maps every alternative", "[core][value]") {
    CHECK(value_type_of(Value(true)) == ValueType::Bool);
    CHECK(value_type_of(Value(1)) == ValueType::Int);
    CHECK(value_type_of(Value(1.0f)) == ValueType::Float);
    CHECK(value_type_of(Value(Vec2{})) == ValueType::Vec2);
    CHECK(value_type_of(Value(Vec3{})) == ValueType::Vec3);
    CHECK(value_type_of(Value(Vec4{})) == ValueType::Vec4);
    CHECK(value_type_of(Value(Color{})) == ValueType::Color);
    // String/Enum/Ref all report String; the spec decides which is meant.
    CHECK(value_type_of(Value(std::string("x"))) == ValueType::String);
    CHECK(value_type_of(Value(Curve{})) == ValueType::Curve);
    CHECK(value_type_of(Value(Gradient{})) == ValueType::Gradient);
    CHECK(value_type_of(Value(std::vector<float>{})) == ValueType::FloatList);
    CHECK(value_type_of(Value(std::vector<Vec3>{})) == ValueType::Vec3List);
    CHECK(value_type_of(Value(nlohmann::json::object())) == ValueType::Json);
}

TEST_CASE("value_compatible follows the coercion rules", "[core][value]") {
    CHECK(value_compatible(Value(3), ValueType::Float));        // int -> float
    CHECK_FALSE(value_compatible(Value(3.5f), ValueType::Int)); // not the other way
    CHECK(value_compatible(Value(std::string("a")), ValueType::Enum));
    CHECK(value_compatible(Value(std::string("a")), ValueType::Ref));
    CHECK(value_compatible(Value(std::string("a")), ValueType::String));
    CHECK(value_compatible(Value(Vec3{}), ValueType::Vec3));
    CHECK_FALSE(value_compatible(Value(Vec3{}), ValueType::Color));
    CHECK_FALSE(value_compatible(Value(Vec4{}), ValueType::Color));
    // json is the catch-all target type
    for (const auto& [type, value] : sample_values()) {
        (void)type;
        CHECK(value_compatible(value, ValueType::Json));
    }
    // a raw json value does not satisfy a typed slot
    CHECK_FALSE(value_compatible(Value(nlohmann::json(1)), ValueType::Float));
}

TEST_CASE("value_as_* coerce and throw E005 on mismatch", "[core][value]") {
    CHECK(value_as_float(Value(2)) == 2.0f);
    CHECK(value_as_float(Value(2.5f)) == 2.5f);
    CHECK(value_as_int(Value(4.0f)) == 4);  // integral float -> int
    CHECK(value_as_int(Value(4)) == 4);
    CHECK(value_as_bool(Value(false)) == false);
    CHECK(value_as_vec3(Value(Vec3{1, 2, 3})) == Vec3{1, 2, 3});
    CHECK(value_as_string(Value(std::string("s"))) == "s");
    CHECK(value_as_float_list(Value(std::vector<float>{1.0f})).size() == 1);
    CHECK(value_as_vec3_list(Value(std::vector<Vec3>{{1, 1, 1}})).size() == 1);
    CHECK(value_as_curve(Value(Curve::constant(2.0f))).keys.size() == 2);
    CHECK(value_as_gradient(Value(Gradient::constant(Color::white()))).keys.size() == 2);

    auto is_e005 = [](const Error& e) { return e.code() == "E005"; };
    CHECK_THROWS_AS(value_as_float(Value(std::string("x"))), Error);
    CHECK_THROWS_AS(value_as_int(Value(4.5f)), Error);
    CHECK_THROWS_AS(value_as_bool(Value(1)), Error);
    CHECK_THROWS_AS(value_as_color(Value(Vec4{1, 1, 1, 1})), Error);
    try {
        value_as_vec2(Value(3));
        FAIL("expected an E005 throw");
    } catch (const Error& e) {
        CHECK(is_e005(e));
    }
}

TEST_CASE("lerp_value is componentwise for numerics and step otherwise", "[core][value]") {
    CHECK_THAT(std::get<float>(lerp_value(Value(0.0f), Value(10.0f), 0.25f)), WithinAbs(2.5, 1e-6));
    CHECK(std::get<int>(lerp_value(Value(0), Value(10), 0.5f)) == 5);
    CHECK(std::get<Vec3>(lerp_value(Value(Vec3{0, 0, 0}), Value(Vec3{2, 4, 6}), 0.5f)) == Vec3{1, 2, 3});
    Color mixed = std::get<Color>(lerp_value(Value(Color{0, 0, 0, 0}), Value(Color{1, 1, 1, 1}), 0.5f));
    CHECK_THAT(mixed.r, WithinAbs(0.5, 1e-6));
    CHECK_THAT(mixed.a, WithinAbs(0.5, 1e-6));
    // mixed int/float promotes to float
    CHECK(value_type_of(lerp_value(Value(0), Value(1.0f), 0.5f)) == ValueType::Float);
    // strings step: hold a until t reaches 1
    CHECK(std::get<std::string>(lerp_value(Value(std::string("a")), Value(std::string("b")), 0.99f)) == "a");
    CHECK(std::get<std::string>(lerp_value(Value(std::string("a")), Value(std::string("b")), 1.0f)) == "b");
}

TEST_CASE("values_equal handles int/float and structured values", "[core][value]") {
    CHECK(values_equal(Value(2), Value(2.0f)));
    CHECK_FALSE(values_equal(Value(2), Value(2.5f)));
    CHECK(values_equal(Value(Curve::linear(0, 1)), Value(Curve::linear(0, 1))));
    CHECK_FALSE(values_equal(Value(Curve::linear(0, 1)), Value(Curve::linear(0, 2))));
    CHECK(values_equal(Value(std::string("a")), Value(std::string("a"))));
    CHECK_FALSE(values_equal(Value(std::string("a")), Value(1)));
    CHECK(values_equal(Value(std::vector<Vec3>{{1, 2, 3}}), Value(std::vector<Vec3>{{1, 2, 3}})));
}

TEST_CASE("value json round trips for every value type", "[core][value]") {
    for (const auto& [type, value] : sample_values()) {
        CAPTURE(to_string(type));
        nlohmann::json encoded = value_to_json(value);
        Value decoded = value_from_json(encoded, type);
        CHECK(values_equal(decoded, value));
        // and re-encoding is a fixed point
        CHECK(value_to_json(decoded) == encoded);
    }
}

TEST_CASE("float encoding stays compact and lossless", "[core][value]") {
    CHECK(value_to_json(Value(0.09f)) == 0.09);
    CHECK(value_to_json(Value(9.81f)) == 9.81);
    CHECK(value_to_json(Value(1.0f)) == 1.0);
    CHECK(value_to_json(Value(0.0001f)) == 0.0001);
    for (float f : {0.0f, 1.0f, -2.5f, 0.09f, 1e-5f, 123456.78f, 3.14159265f}) {
        CAPTURE(f);
        CHECK(std::get<float>(value_from_json(value_to_json(Value(f)), ValueType::Float)) == f);
    }
}

TEST_CASE("color accepts arrays and hex, and encodes compactly", "[core][value]") {
    CHECK(std::get<Color>(value_from_json(nlohmann::json::array({1, 0.5, 0.25}), ValueType::Color)) ==
          Color{1.0f, 0.5f, 0.25f, 1.0f});  // alpha defaults to 1
    Color rgba = std::get<Color>(value_from_json(nlohmann::json::array({1, 0.5, 0.25, 0.5}), ValueType::Color));
    CHECK_THAT(rgba.a, WithinAbs(0.5, 1e-6));

    Color white = std::get<Color>(value_from_json("#ffffff", ValueType::Color));
    CHECK_THAT(white.r, WithinAbs(1.0, 1e-5));
    CHECK_THAT(white.a, WithinAbs(1.0, 1e-6));
    Color half = std::get<Color>(value_from_json("#808080", ValueType::Color));
    CHECK_THAT(half.r, WithinAbs(srgb_to_linear(128.0f / 255.0f), 1e-6));  // sRGB decoded to linear
    CHECK(half.r < 0.5f);
    Color with_alpha = std::get<Color>(value_from_json("#00000080", ValueType::Color));
    CHECK_THAT(with_alpha.a, WithinAbs(128.0 / 255.0, 1e-6));
    CHECK(std::get<Color>(value_from_json("ff0000", ValueType::Color)).r > 0.99f);  // '#' optional

    // opaque colors drop the alpha component on output
    CHECK(value_to_json(Value(Color{1, 0, 0, 1})).size() == 3);
    CHECK(value_to_json(Value(Color{1, 0, 0, 0.5f})).size() == 4);
    CHECK_THROWS_AS(value_from_json("#xyz", ValueType::Color), Error);
    CHECK_THROWS_AS(value_from_json(nlohmann::json::array({1, 0}), ValueType::Color), Error);
}

TEST_CASE("curve accepts both encodings and sorts keys", "[core][value]") {
    Curve shorthand = std::get<Curve>(value_from_json(nlohmann::json::parse("[[0.0,1.0],[1.0,0.0]]"), ValueType::Curve));
    REQUIRE(shorthand.keys.size() == 2);
    CHECK(shorthand.keys[0].v == 1.0f);
    CHECK(shorthand.keys[1].interp == Interp::Linear);

    Curve with_interp =
        std::get<Curve>(value_from_json(nlohmann::json::parse(R"([[0,0,"step"],[0.5,1,"smooth"],[1,0]])"), ValueType::Curve));
    REQUIRE(with_interp.keys.size() == 3);
    CHECK(with_interp.keys[0].interp == Interp::Step);
    CHECK(with_interp.keys[1].interp == Interp::Smooth);
    CHECK(with_interp.keys[2].interp == Interp::Linear);
    // non-linear interps force the 3-element output form
    CHECK(value_to_json(Value(with_interp))[0].size() == 3);
    CHECK(value_to_json(Value(Curve::linear(0, 1)))[0].size() == 2);

    Curve object_form = std::get<Curve>(
        value_from_json(nlohmann::json::parse(R"({"keys":[{"t":0,"v":2},{"t":1,"v":3,"interp":"smooth"}]})"), ValueType::Curve));
    REQUIRE(object_form.keys.size() == 2);
    CHECK(object_form.keys[1].v == 3.0f);
    CHECK(object_form.keys[1].interp == Interp::Smooth);

    // input keys are sorted (stably) by t
    Curve unsorted = std::get<Curve>(value_from_json(nlohmann::json::parse("[[1,10],[0,0],[0.5,5]]"), ValueType::Curve));
    REQUIRE(unsorted.keys.size() == 3);
    CHECK(unsorted.keys[0].t == 0.0f);
    CHECK(unsorted.keys[1].t == 0.5f);
    CHECK(unsorted.keys[2].t == 1.0f);

    CHECK(value_to_json(Value(Curve{})) == nlohmann::json::array());
    CHECK_THROWS_AS(value_from_json(nlohmann::json::parse("[[0]]"), ValueType::Curve), Error);
    CHECK_THROWS_AS(value_from_json(nlohmann::json::parse(R"({"nope":1})"), ValueType::Curve), Error);
}

TEST_CASE("gradient accepts both encodings", "[core][value]") {
    Gradient shorthand = std::get<Gradient>(
        value_from_json(nlohmann::json::parse("[[0.0,[1,0,0,1]],[1.0,[0,0,1,1]]]"), ValueType::Gradient));
    REQUIRE(shorthand.keys.size() == 2);
    CHECK(shorthand.keys[0].color == Color{1, 0, 0, 1});
    CHECK(shorthand.keys[1].color == Color{0, 0, 1, 1});

    Gradient object_form = std::get<Gradient>(value_from_json(
        nlohmann::json::parse(R"({"keys":[{"t":0,"color":"#ff0000"},{"t":1,"color":[0,1,0]}]})"), ValueType::Gradient));
    REQUIRE(object_form.keys.size() == 2);
    CHECK(object_form.keys[0].color.r > 0.99f);
    CHECK(object_form.keys[1].color.g == 1.0f);

    // output is always the compact [[t, color], ...] form
    nlohmann::json out = value_to_json(Value(shorthand));
    REQUIRE(out.is_array());
    CHECK(out[0].is_array());
    CHECK(out[0][0] == 0.0);
    CHECK(out[0][1].is_array());
    CHECK(value_to_json(Value(Gradient{})) == nlohmann::json::array());
}

TEST_CASE("value_from_json rejects malformed input with E005", "[core][value]") {
    auto expect_e005 = [](const nlohmann::json& j, ValueType t) {
        try {
            value_from_json(j, t);
            FAIL("expected E005");
        } catch (const Error& e) {
            CHECK(e.code() == "E005");
        }
    };
    expect_e005(nlohmann::json("x"), ValueType::Float);
    expect_e005(nlohmann::json(1), ValueType::Bool);
    expect_e005(nlohmann::json(1.5), ValueType::Int);
    expect_e005(nlohmann::json::array({1, 2}), ValueType::Vec3);
    expect_e005(nlohmann::json(1), ValueType::String);
    expect_e005(nlohmann::json::parse(R"(["a"])"), ValueType::FloatList);
    expect_e005(nlohmann::json::parse("[[1,2]]"), ValueType::Vec3List);
    // json accepts anything
    CHECK_NOTHROW(value_from_json(nlohmann::json::parse(R"({"anything":[1,2,3]})"), ValueType::Json));
    CHECK_NOTHROW(value_from_json(nlohmann::json("a string"), ValueType::Json));
}

TEST_CASE("parameter eval clamps and honours per-key interpolation", "[core][value]") {
    Parameter constant{Value(3.0f)};
    CHECK_FALSE(constant.animated());
    CHECK(std::get<float>(constant.eval(0.0)) == 3.0f);
    CHECK(std::get<float>(constant.eval(99.0)) == 3.0f);

    Parameter p{Value(-1.0f)};
    p.set_keyframe(1.0, Value(0.0f));
    p.set_keyframe(2.0, Value(10.0f));
    p.set_keyframe(3.0, Value(20.0f), Interp::Step);
    p.set_keyframe(4.0, Value(0.0f));
    REQUIRE(p.animated());
    REQUIRE(p.track.size() == 4);

    // before the first key the value is the first key's value, not `value`
    CHECK_THAT(std::get<float>(p.eval(0.0)), WithinAbs(0.0, 1e-6));
    CHECK_THAT(std::get<float>(p.eval(-5.0)), WithinAbs(0.0, 1e-6));
    CHECK_THAT(std::get<float>(p.eval(1.0)), WithinAbs(0.0, 1e-6));
    CHECK_THAT(std::get<float>(p.eval(1.5)), WithinAbs(5.0, 1e-5));   // linear
    CHECK_THAT(std::get<float>(p.eval(2.0)), WithinAbs(10.0, 1e-6));
    CHECK_THAT(std::get<float>(p.eval(3.5)), WithinAbs(20.0, 1e-6));  // step holds
    CHECK_THAT(std::get<float>(p.eval(3.99)), WithinAbs(20.0, 1e-6));
    CHECK_THAT(std::get<float>(p.eval(4.0)), WithinAbs(0.0, 1e-6));
    CHECK_THAT(std::get<float>(p.eval(100.0)), WithinAbs(0.0, 1e-6));  // after the last key

    Parameter smooth{Value(0.0f)};
    smooth.set_keyframe(0.0, Value(0.0f), Interp::Smooth);
    smooth.set_keyframe(1.0, Value(1.0f));
    CHECK_THAT(std::get<float>(smooth.eval(0.5)), WithinAbs(0.5, 1e-6));
    CHECK(std::get<float>(smooth.eval(0.25)) < 0.25f);  // eased in
    CHECK(std::get<float>(smooth.eval(0.75)) > 0.75f);

    Parameter vectors{Value(Vec3{})};
    vectors.set_keyframe(0.0, Value(Vec3{0, 0, 0}));
    vectors.set_keyframe(2.0, Value(Vec3{0, 0, 4}));
    CHECK(std::get<Vec3>(vectors.eval(1.0)) == Vec3{0, 0, 2});
}

TEST_CASE("set_keyframe inserts sorted, replaces near-duplicates, remove works", "[core][value]") {
    Parameter p{Value(0.0f)};
    p.set_keyframe(2.0, Value(2.0f));
    p.set_keyframe(0.0, Value(0.0f));
    p.set_keyframe(1.0, Value(1.0f));
    REQUIRE(p.track.size() == 3);
    CHECK(p.track[0].time == 0.0);
    CHECK(p.track[1].time == 1.0);
    CHECK(p.track[2].time == 2.0);

    // replacement within 1e-9
    p.set_keyframe(1.0 + 1e-12, Value(9.0f), Interp::Step);
    REQUIRE(p.track.size() == 3);
    CHECK(std::get<float>(p.track[1].value) == 9.0f);
    CHECK(p.track[1].interp == Interp::Step);
    // outside the tolerance is a new key
    p.set_keyframe(1.0 + 1e-6, Value(8.0f));
    CHECK(p.track.size() == 4);

    CHECK(p.remove_keyframe(2.0));
    CHECK(p.track.size() == 3);
    CHECK_FALSE(p.remove_keyframe(42.0));
    CHECK(p.remove_keyframe(1.0000001, 1e-3));  // tolerance honoured
    CHECK(p.track.size() == 2);
}

TEST_CASE("parameter json handles bare and wrapped forms", "[core][value]") {
    // bare
    Parameter bare = parameter_from_json(nlohmann::json(2.5), ValueType::Float);
    CHECK_FALSE(bare.animated());
    CHECK(std::get<float>(bare.value) == 2.5f);
    CHECK(parameter_to_json(bare) == 2.5);

    // wrapped
    Parameter wrapped = parameter_from_json(
        nlohmann::json::parse(R"({"value":1.0,"track":[{"time":0,"value":0},{"time":0.5,"value":2,"interp":"step"}]})"),
        ValueType::Float);
    REQUIRE(wrapped.track.size() == 2);
    CHECK(std::get<float>(wrapped.value) == 1.0f);
    CHECK(wrapped.track[1].interp == Interp::Step);
    nlohmann::json encoded = parameter_to_json(wrapped);
    REQUIRE(encoded.is_object());
    CHECK(encoded.contains("value"));
    CHECK(encoded.at("track").size() == 2);
    CHECK_FALSE(encoded.at("track")[0].contains("interp"));  // linear is the default
    CHECK(encoded.at("track")[1].at("interp") == "step");
    CHECK(parameter_from_json(encoded, ValueType::Float) == wrapped);

    // curve/gradient objects use "keys" and are never mistaken for the wrapper
    Parameter curve = parameter_from_json(nlohmann::json::parse(R"({"keys":[{"t":0,"v":1}]})"), ValueType::Curve);
    CHECK_FALSE(curve.animated());
    CHECK(value_as_curve(curve.value).keys.size() == 1);

    // a json parameter is always taken verbatim, even when it has a "value" key
    Parameter raw = parameter_from_json(nlohmann::json::parse(R"({"value":3,"other":1})"), ValueType::Json);
    CHECK_FALSE(raw.animated());
    CHECK(std::get<nlohmann::json>(raw.value).at("other") == 1);

    // a track alone takes its constant from the first keyframe
    Parameter track_only = parameter_from_json(nlohmann::json::parse(R"({"track":[{"time":1,"value":7}]})"), ValueType::Float);
    CHECK(std::get<float>(track_only.value) == 7.0f);
    CHECK_THROWS_AS(parameter_from_json(nlohmann::json::parse(R"({"track":[]})"), ValueType::Float), Error);
    CHECK_THROWS_AS(parameter_from_json(nlohmann::json::parse(R"({"value":1,"track":3})"), ValueType::Float), Error);
    CHECK_THROWS_AS(parameter_from_json(nlohmann::json::parse(R"({"value":1,"track":[{"time":0}]})"), ValueType::Float), Error);
}

TEST_CASE("parameter equality compares value, times and interps", "[core][value]") {
    Parameter a{Value(1.0f)};
    Parameter b{Value(1)};
    CHECK(a == b);  // int/float are equal by value
    a.set_keyframe(0.5, Value(2.0f));
    CHECK_FALSE(a == b);
    b.set_keyframe(0.5, Value(2.0f));
    CHECK(a == b);
    b.track[0].interp = Interp::Step;
    CHECK_FALSE(a == b);
}
