@rem compiler proof: VS 2022 vcvars32 pinned to MSVC 14.44 and Windows SDK 10.0.28000.0 (the target toolchain)
@call "%ProgramFiles%\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars32.bat" 10.0.28000.0 -vcvars_ver=14.44 %*
