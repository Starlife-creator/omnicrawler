
#define _GNU_SOURCE
#include <signal.h>
#include <Python.h>
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <linux/seccomp.h>
#include <linux/filter.h>
#include <asm/unistd.h>
#include <linux/audit.h>
#include <stddef.h>
#include <unistd.h>
struct attr { uint64_t handled_access_fs, handled_access_net, scoped; };
struct beneath { uint64_t allowed_access; int32_t parent_fd; };
static int add_rule(int ruleset, uint64_t access, const char *text) {
  int fd = open(text, O_PATH | O_CLOEXEC);
  if (fd < 0) return -1;
  struct beneath rule = {access, fd};
  int result = (int)syscall(SYS_landlock_add_rule, ruleset, 1, &rule, 0);
  int saved = errno; close(fd); errno = saved;
  return result;
}
static PyObject *install(PyObject *self, PyObject *args) {
  unsigned long long handled; PyObject *read_paths, *write_paths;
  if (!PyArg_ParseTuple(args, "KOO", &handled, &read_paths, &write_paths)) return NULL;
  struct attr attr = {(uint64_t)handled, (1ULL << 2) - 1, 0};
  int ruleset = (int)syscall(SYS_landlock_create_ruleset, &attr, 16, 0);
  if (ruleset < 0 && errno == EINVAL) ruleset = (int)syscall(SYS_landlock_create_ruleset, &attr, 16, 0);
  if (ruleset < 0 && errno == EINVAL) ruleset = (int)syscall(SYS_landlock_create_ruleset, &attr, 8, 0);
  if (ruleset < 0) return PyErr_SetFromErrno(PyExc_OSError);
  /* Read-only subset of the handled file-access bits. ABI v1 order is
   * execute, write_file, read_file, read_dir, remove_dir, remove_file,
   * make_char, make_dir, make_reg, make_sock, make_fifo, make_block,
   * make_sym, refer. Keep only execute/read_file/read_dir/refer for read
   * roots; write-capable paths are granted below with the full handled mask. */
  uint64_t read_access = handled & ((1ULL << 0) | (1ULL << 2) | (1ULL << 3) | (1ULL << 13));
  Py_ssize_t count = PySequence_Size(read_paths);
  for (Py_ssize_t i = 0; i < count; ++i) {
    PyObject *path = PySequence_GetItem(read_paths, i);
    const char *text = PyUnicode_AsUTF8(path);
    if (!text) { Py_XDECREF(path); close(ruleset); return NULL; }
    if (add_rule(ruleset, read_access, text) < 0) { Py_DECREF(path); close(ruleset); return PyErr_SetFromErrno(PyExc_OSError); }
    Py_DECREF(path);
  }
  count = PySequence_Size(write_paths);
  for (Py_ssize_t i = 0; i < count; ++i) {
    PyObject *path = PySequence_GetItem(write_paths, i);
    const char *text = PyUnicode_AsUTF8(path);
    if (!text) { Py_XDECREF(path); close(ruleset); return NULL; }
    if (add_rule(ruleset, (uint64_t)handled, text) < 0) { Py_DECREF(path); close(ruleset); return PyErr_SetFromErrno(PyExc_OSError); }
    Py_DECREF(path);
  }
  if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) || syscall(SYS_landlock_restrict_self, ruleset, 0)) {
    int saved = errno; close(ruleset); errno = saved; return PyErr_SetFromErrno(PyExc_OSError);
  }
  close(ruleset); Py_RETURN_NONE;
}
static PyObject *seccomp_install(PyObject *self, PyObject *args) {
  Py_buffer view;
  if (!PyArg_ParseTuple(args, "y*", &view)) return NULL;
  if (view.len % (Py_ssize_t)sizeof(struct sock_filter) != 0) {
    PyBuffer_Release(&view); return PyErr_Format(PyExc_ValueError, "invalid BPF program length");
  }
  struct sock_fprog program = {(unsigned short)(view.len / sizeof(struct sock_filter)), (struct sock_filter *)view.buf};
  int result = (int)syscall(__NR_seccomp, SECCOMP_SET_MODE_FILTER, 0UL, &program);
  PyBuffer_Release(&view);
  if (result < 0) return PyErr_SetFromErrno(PyExc_OSError);
  Py_RETURN_NONE;
}
static PyMethodDef methods[] = {{"install", install, METH_VARARGS}, {"seccomp_install", seccomp_install, METH_VARARGS}, {NULL, NULL, 0, NULL}};
static struct PyModuleDef module = {PyModuleDef_HEAD_INIT, "omnicrawler_landlock", NULL, -1, methods};
PyMODINIT_FUNC PyInit_omnicrawler_landlock(void) { return PyModule_Create(&module); }
