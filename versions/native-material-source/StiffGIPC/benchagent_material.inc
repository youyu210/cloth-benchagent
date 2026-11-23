// BenchAgent material adapter. The solver continues to use the original FEM setup.
gipc::Json bench_material_defaults;
gipc::Json bench_material_request;

gipc::Json bench_cloth_values()
{
    return {{"young_pa", ipc.clothYoungModulus},
            {"density_kg_m3", ipc.clothDensity},
            {"poisson_ratio", ipc.PoissonRate}};
}

void apply_bench_material()
{
    const char* path = std::getenv("CIPC_MATERIAL_CONFIG");
    if(!path)
        return;

    const int scene = gipc::benchmark::options.scene;
    const std::string selector = std::getenv("CIPC_SCENE") ? cipc_case : "";
    const bool pure_cloth = (selector.empty() && scene == 3) || (selector == "hang" && scene == 1);
    const bool sphere_cloth = selector == "sphere7" && scene == 1;
    if(!pure_cloth && !sphere_cloth)
        throw std::runtime_error("Material overrides unavailable for this scene");

    std::ifstream input(path);
    const auto config = gipc::Json::parse(input);
    const std::string scene_id = sphere_cloth ? "cloth-sphere7" : selector == "hang" ? "cloth-hang" : "fixed-cloth";
    if(!config.is_object() || config.size() != 4
       || config.at("schema") != "benchagent-cloth-material-v1"
       || config.at("scene") != scene_id || config.at("object") != "cloth"
       || !config.at("values").is_object())
        throw std::runtime_error("Invalid BenchAgent material configuration");

    bench_material_defaults = bench_cloth_values();
    bench_material_request = config;
    for(const auto& entry : config.at("values").items())
    {
        double* field = nullptr;
        double low = 0.0, high = 0.0;
        if(entry.key() == "young_pa")
        {
            field = &ipc.clothYoungModulus;
            low = 1000.0;
            high = 1e7;
        }
        else if(entry.key() == "density_kg_m3")
        {
            field = &ipc.clothDensity;
            low = 10.0;
            high = 2000.0;
        }
        else if(entry.key() == "poisson_ratio" && pure_cloth)
        {
            field = &ipc.PoissonRate;
            low = 0.0;
            high = 0.49;
        }
        if(!field || !entry.value().is_number())
            throw std::runtime_error("Unsupported BenchAgent material field");
        const double value = entry.value().get<double>();
        if(!std::isfinite(value) || value < low || value > high)
            throw std::runtime_error("BenchAgent material field outside allowed range");
        *field = value;
    }
}

void save_bench_material(const std::filesystem::path& output,
                         const std::vector<double>& gpu_masses)
{
    if(bench_material_request.is_null())
        return;
    const auto scene = bench_material_request.at("scene");
    size_t count = 0;
    double mass = 0.0;
    for(size_t i = 0; i < gpu_masses.size(); ++i)
    {
        if(scene == "fixed-cloth" || cipc_roles.at(i) == 1)
        {
            ++count;
            mass += gpu_masses[i];
        }
    }
    const gipc::Json derived = {
        {"stretch_stiff", ipc.stretchStiff}, {"shear_stiff", ipc.shearStiff},
        {"bend_stiff", ipc.bendStiff}, {"thickness_m", ipc.clothThickness},
        {"areal_density_kg_m2", ipc.clothDensity * ipc.clothThickness},
        {"bending_young_pa", ipc.bendYoungModulus}, {"strain_rate", ipc.strainRate},
        {"friction", ipc.frictionRate}, {"ground_friction", ipc.gd_frictionRate},
        {"dt_s", ipc.IPC_dt}, {"cloth_vertex_count", count}, {"gpu_cloth_mass_kg", mass}
    };
    std::ofstream file(output / "material_effective.json");
    file << gipc::Json{{"request", bench_material_request}, {"defaults", bench_material_defaults},
                      {"effective", bench_cloth_values()}, {"derived", derived}}.dump(2) << '\n';
    file.close();
    if(!file)
        throw std::runtime_error("BenchAgent material evidence could not be saved");
}
