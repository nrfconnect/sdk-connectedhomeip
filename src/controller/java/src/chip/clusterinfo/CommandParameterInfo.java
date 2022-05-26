package chip.clusterinfo;

/** CommandParameterInfo captures the name and type of a parameter */
public class CommandParameterInfo {
  public CommandParameterInfo() {}

  public CommandParameterInfo(String name, Class<?> type, Class<?> underlyingType) {
    this.name = name;
    this.type = type;
    this.underlyingType = underlyingType;
  }

  public String name;
  public Class<?> type;
  public Class<?> underlyingType;
}
