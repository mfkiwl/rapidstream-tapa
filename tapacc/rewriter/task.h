// Copyright (c) 2024 RapidStream Design Automation, Inc. and contributors.
// All rights reserved. The contributor(s) of this file has/have agreed to the
// RapidStream Contributor License Agreement.

#ifndef TAPA_TASK_H_
#define TAPA_TASK_H_

#include <map>
#include <memory>
#include <optional>
#include <queue>
#include <set>
#include <vector>

#include "clang/AST/AST.h"
#include "clang/AST/Mangle.h"
#include "clang/AST/RecursiveASTVisitor.h"
#include "clang/Basic/SourceManager.h"
#include "clang/Rewrite/Core/Rewriter.h"

#include "nlohmann/json.hpp"

#include "../target/all_targets.h"
#include "stream.h"

namespace tapa {
namespace internal {

const clang::ExprWithCleanups* GetTapaTaskObjectExpr(
    const clang::Stmt* func_body);

std::vector<const clang::CXXMemberCallExpr*> GetTapaInvokes(
    const clang::Stmt* task);

// A TAPA task is a function that is invoked by a TAPA task::task.invoke call.
// It can be either a function or a template specialization of a function.
// The specialization of a TAPA task function and the function invoking this
// specialization. As the wrapper must be generated right before the invoker,
// so that the context of the invoker is available, each specialization is
// associated with the invoker function that is responsible for generating
// the wrapper.
struct TapaTask {
  const clang::FunctionDecl* func;
  const clang::FunctionDecl* invoker_func;
  const bool is_template_specialization;

  TapaTask(const clang::FunctionDecl* f,
           const clang::FunctionDecl* invoker_func = nullptr,
           const bool is_template_specialization = false)
      : func(f),
        invoker_func(invoker_func),
        is_template_specialization(is_template_specialization) {}

  bool operator<(const TapaTask& other) const {
    return std::tie(func, is_template_specialization) <
           std::tie(other.func, is_template_specialization);
  }
};

class Visitor : public clang::RecursiveASTVisitor<Visitor> {
 public:
  explicit Visitor(clang::ASTContext& context,
                   std::vector<const clang::FunctionDecl*>& funcs,
                   std::set<TapaTask>& tapa_tasks,
                   std::map<TapaTask, clang::Rewriter>& rewriters,
                   std::map<TapaTask, nlohmann::json>& metadata)
      : context_{context},
        mangling_context_(clang::ItaniumMangleContext::create(
            context, context.getDiagnostics())),
        funcs_{funcs},
        tapa_tasks_{tapa_tasks},
        rewriters_{rewriters},
        metadata_{metadata} {}

  bool VisitAttributedStmt(clang::AttributedStmt* stmt);
  bool VisitFunctionDecl(clang::FunctionDecl* func);

  void VisitTask(const TapaTask& task);

  std::string GetMangledFuncName(const clang::FunctionDecl* func) {
    std::string name;
    llvm::raw_string_ostream os(name);
    os << "tapa_mangled";  // Otherwise, Vivado will complain about the
                           // function name being started with _.
    mangling_context_->mangleName(func, os);
    os.flush();
    return os.str();
  }

  std::string GetTemplatedFuncName(const clang::FunctionDecl* func) {
    std::string name;
    llvm::raw_string_ostream os(name);
    auto p = context_.getPrintingPolicy();
    func->getNameForDiagnostic(os, p, /*qualified=*/true);
    os.flush();
    return os.str();
  }

  std::string GenerateWrapperCode(const TapaTask& task) {
    std::string code;
    llvm::raw_string_ostream os(code);

    // Avoid printing the tag keyword (`class` tapa::mmap) in the wrapper
    // since the target may have a different definition of the TAPA types.
    clang::PrintingPolicy policy = context_.getPrintingPolicy();
    policy.SuppressTagKeyword = true;

    os << "\n\nvoid " << GetMangledFuncName(task.func) << "(";
    // TAPA parameters are guaranteed to be in the format of type_name
    // variable_name. Therefore, simply iterate through the parameters
    // and append the type and name to the function signature works.
    for (size_t i = 0; i < task.func->getNumParams(); ++i) {
      if (i > 0) os << ", ";
      os << task.func->getParamDecl(i)->getType().getAsString(policy);
      os << " " << task.func->getParamDecl(i)->getNameAsString();
    }
    os << ") {\n";

    // Add target-dependent code for wrapper parameters.
    // TODO: Currently it only supports adding code for lower-level wrapper
    // tasks.
    auto add_line = [&os](llvm::StringRef line) { os << "  " << line << "\n"; };
    auto add_pragma = [&os](std::initializer_list<llvm::StringRef> args) {
      os << "  #pragma " << llvm::join(args, " ") << "\n";
    };
    for (size_t i = 0; i < task.func->getNumParams(); ++i) {
      current_target->AddCodeForLowerLevelParameter(task.func->getParamDecl(i),
                                                    add_line, add_pragma);
    }

    os << "  " << GetTemplatedFuncName(task.func) << "(";

    for (size_t i = 0; i < task.func->getNumParams(); ++i) {
      if (i > 0) os << ", ";
      os << task.func->getParamDecl(i)->getNameAsString();
    }
    os << ");\n}\n";
    return os.str();
  }

  // Indicate whether the current traversal is the first one to obtain the
  // full list of functions.
  bool is_first_traversal = true;

 private:
  static thread_local const clang::FunctionDecl* rewriting_func;
  static thread_local const TapaTask* current_task;
  static thread_local Target* current_target;

  clang::ASTContext& context_;
  clang::MangleContext* mangling_context_;
  std::vector<const clang::FunctionDecl*>& funcs_;
  std::set<TapaTask>& tapa_tasks_;

  std::map<TapaTask, clang::Rewriter>& rewriters_;
  std::map<TapaTask, nlohmann::json>& metadata_;

  clang::Rewriter& GetRewriter() { return rewriters_[*current_task]; }
  nlohmann::json& GetMetadata() {
    if (metadata_[*current_task].is_null())
      metadata_[*current_task] = nlohmann::json::object();
    return metadata_[*current_task];
  }

  void ProcessUpperLevelTaskFunc(const clang::ExprWithCleanups* task_obj,
                                 const clang::FunctionDecl* func);
  void ProcessLowerLevelTaskFunc(const clang::FunctionDecl* func);
  void ProcessOtherFunc(const clang::FunctionDecl* func);
  void ProcessTaskPorts(const TapaTask& task, nlohmann::json& metadata);

  clang::CharSourceRange GetCharSourceRange(const clang::Stmt* stmt);
  clang::CharSourceRange GetCharSourceRange(clang::SourceRange range);
  clang::SourceLocation GetEndOfLoc(clang::SourceLocation loc);

  bool isFuncTapaTask(const clang::FunctionDecl* func);

  int64_t EvalAsInt(const clang::Expr* expr);
  int GetTypeWidth(const clang::QualType type) {
    return context_.getTypeSize(type);
  }

  template <typename T>
  void HandleAttrOnNodeWithBody(const T* node, const clang::Stmt* body,
                                llvm::ArrayRef<const clang::Attr*> attrs);
};

inline bool IsFuncIgnored(const clang::FunctionDecl* func) {
  if (auto attr = func->getAttr<clang::TapaTargetAttr>()) {
    if (attr->getTarget() == clang::TapaTargetAttr::TargetType::Ignore) {
      return true;
    }
  }
  return false;
}

// Find for a given upper-level task, return all direct children tasks (e.g.
// tasks instanciated directly in upper).
// Lower-level tasks or non-task functions return an empty vector.
inline std::vector<TapaTask> FindChildrenTasks(
    const clang::FunctionDecl* upper_func) {
  // when a function is ignored, it does not have any children.
  if (IsFuncIgnored(upper_func)) {
    return {};
  }

  auto body = upper_func->getBody();
  if (auto task = GetTapaTaskObjectExpr(body)) {
    auto invokes = GetTapaInvokes(task);
    std::vector<TapaTask> tasks;
    for (auto invoke : invokes) {
      // Dynamic cast correctness is guaranteed by tapa.h.
      if (auto decl_ref =
              llvm::dyn_cast<clang::DeclRefExpr>(invoke->getArg(0))) {
        auto func_decl =
            llvm::dyn_cast<clang::FunctionDecl>(decl_ref->getDecl());

        // skip function definitions that has no body.
        if (!func_decl->isThisDeclarationADefinition()) continue;

        // If the function is a template instantiation, get the specialization
        // information.
        if (func_decl->isFunctionTemplateSpecialization()) {
          tasks.push_back(TapaTask(func_decl, upper_func, true));
        } else {
          tasks.push_back(TapaTask(func_decl));
        }
      }
    }
    return tasks;
  }
  return {};
}

// Find all tasks instanciated using breadth-first search.
// If a task is instantiated more than once, it will only appear once.
// Lower-level tasks or non-task functions return an empty vector.
inline std::vector<TapaTask> FindAllTasks(
    const clang::FunctionDecl* root_upper) {
  std::vector<TapaTask> tasks{root_upper};
  std::set<TapaTask> task_set{root_upper};
  std::queue<const clang::FunctionDecl*> task_func_queue;
  task_func_queue.push(root_upper);
  while (!task_func_queue.empty()) {
    auto upper = task_func_queue.front();
    for (auto child : FindChildrenTasks(upper)) {
      if (task_set.count(child) == 0) {
        tasks.push_back(child);
        task_set.insert(child);
        task_func_queue.push(child.func);
      }
    }
    task_func_queue.pop();
  }
  return tasks;
}

// Return the body of a loop stmt or nullptr if the input is not a loop.
inline const clang::Stmt* GetLoopBody(const clang::Stmt* loop) {
  if (loop != nullptr) {
    if (auto stmt = llvm::dyn_cast<clang::DoStmt>(loop)) {
      return stmt->getBody();
    }
    if (auto stmt = llvm::dyn_cast<clang::ForStmt>(loop)) {
      return stmt->getBody();
    }
    if (auto stmt = llvm::dyn_cast<clang::WhileStmt>(loop)) {
      return stmt->getBody();
    }
    if (auto stmt = llvm::dyn_cast<clang::CXXForRangeStmt>(loop)) {
      return stmt->getBody();
    }
  }
  return nullptr;
}

}  // namespace internal
}  // namespace tapa

#endif  // TAPA_TASK_H_
