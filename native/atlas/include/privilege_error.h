#pragma once

#include <string>
#include <utility>
#include <variant>

namespace secdogie::atlas {

enum class PrivilegeCode {
  Ok,
  Stripped,
  DeniedWrite,
  DeniedEmpty,
  RefusedIdentity,
  NotElevated,
  Unsupported,
  BadCommand,
  NotAllowlisted,
  NoSession,
  AccessDenied,
  Failed,
};

inline const char* PrivilegeCodeName(PrivilegeCode c) noexcept {
  switch (c) {
    case PrivilegeCode::Ok: return "ok";
    case PrivilegeCode::Stripped: return "stripped";
    case PrivilegeCode::DeniedWrite: return "denied-write";
    case PrivilegeCode::DeniedEmpty: return "denied-empty";
    case PrivilegeCode::RefusedIdentity: return "refused-identity";
    case PrivilegeCode::NotElevated: return "not-elevated";
    case PrivilegeCode::Unsupported: return "unsupported";
    case PrivilegeCode::BadCommand: return "bad-command";
    case PrivilegeCode::NotAllowlisted: return "not-allowlisted";
    case PrivilegeCode::NoSession: return "no-session";
    case PrivilegeCode::AccessDenied: return "access-denied";
    case PrivilegeCode::Failed: return "failed";
  }
  return "failed";
}

struct PrivilegeError {
  PrivilegeCode code = PrivilegeCode::Failed;
  std::string detail;
};

// Minimal expected<T, PrivilegeError> so we don't take a C++23 dependency.
template <typename T>
class Result {
 public:
  Result(T value) : storage_(std::in_place_index<0>, std::move(value)) {}
  Result(PrivilegeError err) : storage_(std::in_place_index<1>, std::move(err)) {}

  bool ok() const noexcept { return storage_.index() == 0; }
  explicit operator bool() const noexcept { return ok(); }

  const T& value() const { return std::get<0>(storage_); }
  T& value() { return std::get<0>(storage_); }
  const PrivilegeError& error() const { return std::get<1>(storage_); }

  T take() { return std::move(std::get<0>(storage_)); }

 private:
  std::variant<T, PrivilegeError> storage_;
};

}  // namespace secdogie::atlas
